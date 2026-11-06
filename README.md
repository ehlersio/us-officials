# us-officials

An open, free API of who represents each US address, and how they vote. It covers the parts of three
services that shut down: ProPublica's Congress API (2024), Google's Civic Information representatives
lookup (April 2025) and the OpenSecrets API (April 2025).

It covers:

- **Address lookup**: an address or a point gives the two US Senators, the member of the House, and the
  state senator(s) and representative(s) for that location.
- **People**: every current member of Congress and state legislator (50 states, DC, Puerto Rico), with
  contact details and their ids in other databases (FEC, OpenSecrets, Vote Smart, Ballotpedia, Wikidata,
  GovTrack, Open States).
- **Votes**: every House and Senate roll call of the current Congress, and each member's position on each.
- **Sponsored bills** for members of Congress, when the collector runs with a Congress.gov API key.
- **Campaign money** for members of Congress, per two-year cycle, when the collector runs with an
  api.data.gov key: what their campaigns raised, spent and hold, split into individual (donations of $200 or
  less vs. larger), PAC and party money and the candidate's own money, plus the share of itemized individual
  donations from in-state addresses. Only totals are published, never donors' names.

The API states facts and where they came from. It has no scores, ratings or grades: apps built on it can
make those calls.

## Using it

Everything except the lookup is a static JSON file, so the whole API can be mirrored or downloaded at once
(the `api` release has it as one tarball).

| Path | What it is |
|---|---|
| `/v1/lookup?address=...` or `?lat=..&lng=..` | Who represents that location. `POST` a JSON body (`{"address": "..."}`) to keep the address out of URLs and logs. |
| `/v1/index.json` | Counts, sources, build time, the district maps in use |
| `/v1/people/index.json` | One summary row per current officeholder |
| `/v1/people/<id>.json` | One person: role, contact, ids, sources |
| `/v1/people/<id>/votes.json` | A member of Congress's recorded votes |
| `/v1/people/<id>/bills.json` | Bills they sponsored (only when built with a Congress.gov key) |
| `/v1/people/<id>/money.json` | Campaign money per two-year cycle, with notes on what each figure covers (only when built with an api.data.gov key) |
| `/v1/votes/index.json` | Every roll call, without positions |
| `/v1/votes/<house\|senate>/<congress>-<session>-<roll>.json` | One roll call with every member's position |
| `/v1/coverage.json` | Per state: legislators that could not be tied to a Census district, and districts with no officeholder on record |

Person ids are `bioguide-<Bioguide id>` for members of Congress and `openstates-<uuid>` for state legislators.

## Sources

| Data | Source |
|---|---|
| Members of Congress | [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) (CC0) |
| State legislators | [openstates/people](https://github.com/openstates/people) (CC0) |
| House roll calls | [Office of the Clerk](https://clerk.house.gov/Votes), one XML file per roll call |
| Senate roll calls | [Voteview](https://voteview.com/) (UCLA). senate.gov blocks automated downloads, so each Senate vote links to its senate.gov page instead of being fetched from it. |
| Sponsored bills | [Congress.gov API](https://api.congress.gov/) (free api.data.gov key) |
| Campaign money | [OpenFEC API](https://api.open.fec.gov/developers/) (same free api.data.gov key) |
| Districts | [Census geocoder](https://geocoding.geo.census.gov/) at request time, [TIGERweb](https://tigerweb.geo.census.gov/) district tables at build time |

Every person record lists the sources it came from and when they were retrieved. A value a source does not
give is left out, never filled in.

## Campaign money

Each cycle in `money.json` has the totals the candidate's authorized committees reported to the FEC, through
`coverage_end_date`. `individual_unitemized_contributions` is money from donors who gave $200 or less in the
cycle. `itemized_by_state` comes from a different FEC dataset, its processed itemized transactions through
`processed_through`; its dollars do not add up to the reported itemized total, so it is published for the
split between states (`in_state_share`), not for amounts. Leadership PACs and outside spending (super PACs)
are not included.

The FEC allows 1,000 requests an hour per key, so each run reads every member's summary totals in a few
batched requests and fetches full detail only for candidates whose numbers changed, at most 350 per run.
After a filing deadline, it can take a couple of daily runs for every member to update.

## Known gaps

- **Which district maps.** The lookup uses the maps the people now in office were elected under, not maps
  drawn for the next election. For Congress, that is the Congress most House members' terms began in, so the
  switch to new maps happens when congress-legislators lists the new members. For each state legislative
  chamber, it is the newest map that a group of current members (8% of seats, at least 3, sworn in on the
  same day) was seated under; special elections are spread over many days and do not move a chamber.
  `/v1/coverage.json` shows each chamber's map and the group that decided it, and `STATE_MAP_OVERRIDES` in
  `us_officials/config.py` corrects a wrong pick. A chamber is on one map as a whole, so a staggered senate,
  or a chamber where only some seats were re-elected on new lines (as in Mississippi's 2025 court-ordered
  special elections), can be off near the redrawn boundaries. `CONGRESSIONAL_MAPS` needs a row for each new
  Congress, and every build checks that the Census geocoder still serves each map it uses.
- **New Hampshire floterial districts** overlap the base House districts and are not on Census maps, so
  floterial representatives are not returned by the lookup (about 14% of NH's House).
- **Maine's tribal representatives** have no district and are not returned by the lookup.
- **Local officials** (county, city, school board) are not in v0.1.

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m us_officials.run
```

This writes `dist/`. Set `CONGRESS_API_KEY` to include sponsored bills: the first run reads every member's list
(about 25 minutes), later runs only Congress.gov's update feed, with a full re-read once a week. The first run downloads every House
roll call of the Congress (a few minutes); later runs fetch only new ones, from `data/raw/`.

The Worker serves `dist/` and answers `/v1/lookup`:

```bash
npm --prefix worker test
(cd worker && wrangler dev)
```

`.github/workflows/update.yml` runs the collection daily, commits new House roll calls, uploads the tarball
and deploys the Worker when `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are set.

## Corrections

Most records come from the upstream projects above, so a wrong name, party or contact detail is best fixed
there (it reaches this API on the next run). Open an issue here for anything this project gets wrong itself,
such as a lookup returning the wrong people.

## License

Code: MIT. Data: CC0 1.0. The upstream data is CC0 or US government work. Voteview asks that its data be
cited: Lewis, Jeffrey B., Keith Poole, Howard Rosenthal, Adam Boche, Aaron Rudkin, and Luke Sonnet. Voteview:
Congressional Roll-Call Votes Database. https://voteview.com/
