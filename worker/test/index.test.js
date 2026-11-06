import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, describe, it } from "node:test";

import worker, { districtsFrom, geocoderUrl, officialsFor, readInput } from "../src/index.js";

const fixture = (name) => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url)));

const META = {
  congress: 119,
  geocoder_vintage: "ACS2025_Current",
  congressional: "119th Congressional Districts",
  state_legislative: "2024 State Legislative Districts",
};

const person = (id, office, district = null) => ({ id, name: id, office, state: "NC", district, url: `/v1/people/${id}.json` });

const NC = {
  state: "NC",
  has_senators: true,
  senate: ["sen-a", "sen-b"],
  cd: { 3702: ["rep-2"] },
  sldu: { 37014: ["stsen-14"] },
  sldl: { 37038: [] },
  at_large: [],
  people: {
    "sen-a": person("sen-a", "Senator"),
    "sen-b": person("sen-b", "Senator"),
    "rep-2": person("rep-2", "Representative", "2"),
    "stsen-14": person("stsen-14", "State Senate", "14"),
  },
};

// Static assets: lookup/meta.json and lookup/nc.json; everything else 404s.
const env = {
  ASSETS: {
    async fetch(req) {
      const { pathname } = new URL(req.url);
      const files = { "/v1/lookup/meta.json": META, "/v1/lookup/nc.json": NC };
      return pathname in files ? Response.json(files[pathname]) : new Response("not found", { status: 404 });
    },
  },
};

const realFetch = globalThis.fetch;
function geocoderReturns(body, status = 200) {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    return new Response(JSON.stringify(body), { status });
  };
  return calls;
}
afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("readInput", () => {
  it("takes an address from the query string", async () => {
    assert.deepEqual(await readInput(new Request("https://x/v1/lookup?address=1+Main+St")), { address: "1 Main St" });
  });
  it("takes lat/lng from a POST body", async () => {
    const req = new Request("https://x/v1/lookup", { method: "POST", body: JSON.stringify({ lat: 35.78, lng: -78.64 }) });
    assert.deepEqual(await readInput(req), { lat: 35.78, lng: -78.64 });
  });
  it("rejects missing, out-of-range and oversized input", async () => {
    assert.ok((await readInput(new Request("https://x/v1/lookup"))).error);
    assert.ok((await readInput(new Request("https://x/v1/lookup?lat=95&lng=0"))).error);
    assert.ok((await readInput(new Request(`https://x/v1/lookup?address=${"a".repeat(201)}`))).error);
    const bad = new Request("https://x/v1/lookup", { method: "POST", body: "not json" });
    assert.ok((await readInput(bad)).error);
  });
});

describe("geocoderUrl", () => {
  it("uses the vintage from the build", () => {
    assert.match(geocoderUrl({ address: "1 Main St" }, "ACS2025_Current"), /onelineaddress\?address=1%20Main%20St&.*vintage=ACS2025_Current/);
    assert.match(geocoderUrl({ lat: 1, lng: 2 }, "ACS2025_Current"), /coordinates\?x=2&y=1&/);
  });
});

describe("districtsFrom", () => {
  it("reads the districts on the build's maps from an address match", () => {
    const found = districtsFrom(fixture("geocoder-raleigh.json").result, META);
    assert.equal(found.state, "NC");
    assert.equal(found.matched_address, "16 W JONES ST, RALEIGH, NC, 27601");
    assert.deepEqual(found.districts.map((d) => [d.layer, d.geoid]), [["cd", "3702"], ["sldu", "37014"], ["sldl", "37038"]]);
  });
  it("reads a coordinates answer", () => {
    const found = districtsFrom(fixture("geocoder-coords.json").result, META);
    assert.equal(found.state, "NC");
    assert.equal(found.districts.length, 3);
  });
  it("finds nothing on other maps rather than matching the wrong ones", () => {
    const found = districtsFrom(fixture("geocoder-raleigh.json").result, { ...META, congressional: "120th Congressional Districts" });
    assert.deepEqual(found.districts.map((d) => d.layer), ["sldu", "sldl"]);
  });
  it("returns null when nothing matched", () => {
    assert.equal(districtsFrom(fixture("geocoder-nomatch.json").result, META), null);
  });
});

describe("officialsFor", () => {
  it("orders officials and notes an empty seat", () => {
    const found = districtsFrom(fixture("geocoder-raleigh.json").result, META);
    const { officials, notes } = officialsFor(found, NC);
    assert.deepEqual(officials.map((o) => o.id), ["sen-a", "sen-b", "rep-2", "stsen-14"]);
    assert.equal(officials[2].district_geoid, "3702");
    assert.equal(notes.length, 1);
    assert.match(notes[0], /may be vacant/);
  });
  it("skips Senators where there are none and adds at-large seats", () => {
    const table = { ...NC, has_senators: false, at_large: ["rep-2"] };
    const { officials } = officialsFor({ districts: [] }, table);
    assert.deepEqual(officials.map((o) => o.id), ["rep-2"]);
  });
});

describe("fetch handler", () => {
  it("answers a lookup end to end", async () => {
    const calls = geocoderReturns(fixture("geocoder-raleigh.json"));
    const res = await worker.fetch(new Request("https://x/v1/lookup?address=16+W+Jones+St+Raleigh+NC"), env);
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("Cache-Control"), "no-store");
    assert.equal(res.headers.get("Access-Control-Allow-Origin"), "*");
    const body = await res.json();
    assert.equal(body.officials.length, 4);
    assert.equal(calls.length, 1);
  });
  it("is a 404 when the geocoder finds nothing", async () => {
    geocoderReturns(fixture("geocoder-nomatch.json"));
    const res = await worker.fetch(new Request("https://x/v1/lookup?address=nowhere"), env);
    assert.equal(res.status, 404);
  });
  it("is a 502 when the geocoder fails", async () => {
    geocoderReturns({}, 500);
    const res = await worker.fetch(new Request("https://x/v1/lookup?address=1+Main+St"), env);
    assert.equal(res.status, 502);
  });
  it("answers preflight and refuses other methods", async () => {
    assert.equal((await worker.fetch(new Request("https://x/v1/lookup", { method: "OPTIONS" }), env)).status, 204);
    assert.equal((await worker.fetch(new Request("https://x/v1/lookup", { method: "DELETE" }), env)).status, 405);
  });
  it("is a JSON 404 for a missing file", async () => {
    const res = await worker.fetch(new Request("https://x/v1/people/nobody.json"), env);
    assert.equal(res.status, 404);
    assert.equal((await res.json()).error, "not_found");
  });
});
