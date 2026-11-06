// us-officials API Worker.
//
// Everything under /v1/ is a static JSON file served from Workers static assets (dist/). The Worker itself
// only answers /v1/lookup: it asks the Census geocoder which districts an address (or a point) is in, then
// reads that state's lookup/<state>.json to turn district GEOIDs into the people who represent them.
//
// Addresses are never logged or stored, and lookup responses are marked no-store.

const GEOCODER = "https://geocoding.geo.census.gov/geocoder/geographies";
const BENCHMARK = "Public_AR_Current";
const MAX_ADDRESS = 200;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...headers },
  });
}

function error(status, code, message) {
  return json({ error: code, message }, status, { "Cache-Control": "no-store" });
}

async function asset(env, request, path) {
  const res = await env.ASSETS.fetch(new Request(new URL(path, request.url)));
  if (!res.ok) return null;
  return res.json();
}

// The lookup input from a GET query string or a POST JSON body: an address, or lat + lng.
export async function readInput(request) {
  let src = {};
  if (request.method === "POST") {
    try {
      src = await request.json();
    } catch {
      return { error: "Body must be JSON: {\"address\": \"...\"} or {\"lat\": ..., \"lng\": ...}" };
    }
  } else {
    const q = new URL(request.url).searchParams;
    src = { address: q.get("address"), lat: q.get("lat"), lng: q.get("lng") };
  }
  const address = typeof src.address === "string" ? src.address.trim() : "";
  if (address) {
    if (address.length > MAX_ADDRESS) return { error: `address is longer than ${MAX_ADDRESS} characters` };
    return { address };
  }
  const lat = Number(src.lat);
  const lng = Number(src.lng);
  const given = src.lat != null && src.lat !== "" && src.lng != null && src.lng !== "";
  if (given && Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180) {
    return { lat, lng };
  }
  return { error: "Give an address, or lat and lng" };
}

export function geocoderUrl(input, vintage) {
  const common = `benchmark=${BENCHMARK}&vintage=${encodeURIComponent(vintage)}&layers=all&format=json`;
  if (input.address) return `${GEOCODER}/onelineaddress?address=${encodeURIComponent(input.address)}&${common}`;
  return `${GEOCODER}/coordinates?x=${input.lng}&y=${input.lat}&${common}`;
}

// The districts of one geocoder answer, using the layer names the static build says are current, so a
// geocoder answer on other maps is caught instead of being matched to the wrong people.
export function districtsFrom(result, meta) {
  const match = result.addressMatches ? result.addressMatches[0] : null;
  const geos = match ? match.geographies : result.geographies;
  if (!geos || !geos.States || !geos.States[0]) return null;
  const pick = (name) => (geos[name] && geos[name][0]) || null;
  const layer = (kind, name) => {
    const g = pick(name);
    return g ? { layer: kind, geoid: g.GEOID, name: g.NAME } : null;
  };
  return {
    state: geos.States[0].STUSAB,
    matched_address: match ? match.matchedAddress : null,
    coordinates: match ? { lat: match.coordinates.y, lng: match.coordinates.x } : null,
    districts: [
      layer("cd", meta.congressional),
      layer("sldu", `${meta.state_legislative} - Upper`),
      layer("sldl", `${meta.state_legislative} - Lower`),
    ].filter(Boolean),
  };
}

// Officials for the districts found, in a fixed order: Senators, House, state upper, state lower, at-large.
export function officialsFor(found, table) {
  const officials = [];
  const notes = [];
  const add = (ids, district) => {
    for (const id of ids) {
      const person = table.people[id];
      if (person) officials.push({ ...person, district_geoid: district ? district.geoid : null });
    }
  };
  if (table.has_senators) add(table.senate, null);
  for (const d of found.districts) {
    const ids = (table[d.layer] || {})[d.geoid];
    if (ids === undefined) {
      notes.push(`${d.name} (${d.geoid}) is not in this dataset's district table.`);
    } else if (ids.length === 0) {
      notes.push(`No current officeholder on record for ${d.name}: the seat may be vacant. See /v1/coverage.json.`);
    } else {
      add(ids, d);
    }
  }
  add(table.at_large || [], null);
  if (table.known_gap) notes.push(table.known_gap);
  return { officials, notes };
}

async function lookup(request, env) {
  const input = await readInput(request);
  if (input.error) return error(400, "bad_request", input.error);

  const meta = await asset(env, request, "/v1/lookup/meta.json");
  if (!meta) return error(503, "not_built", "The lookup tables are missing from this deployment.");

  let result;
  try {
    const res = await fetch(geocoderUrl(input, meta.geocoder_vintage), { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`geocoder answered ${res.status}`);
    result = (await res.json()).result;
  } catch (e) {
    return error(502, "geocoder_unavailable", `The Census geocoder could not be reached (${e.message}).`);
  }

  const found = districtsFrom(result, meta);
  if (!found) return error(404, "not_found", "The Census geocoder found no match for that location.");

  const table = await asset(env, request, `/v1/lookup/${found.state.toLowerCase()}.json`);
  if (!table) return error(404, "no_data", `No lookup table for ${found.state}.`);

  const { officials, notes } = officialsFor(found, table);
  return json(
    {
      state: found.state,
      matched_address: found.matched_address,
      coordinates: found.coordinates,
      districts: found.districts,
      officials,
      notes,
      district_maps: meta,
      sources: [
        { publisher: "U.S. Census Bureau Geocoder", url: "https://geocoding.geo.census.gov/" },
        { publisher: "us-officials", url: "/v1/index.json" },
      ],
    },
    200,
    { "Cache-Control": "no-store" },
  );
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    if (pathname === "/v1/lookup" || pathname === "/v1/lookup/") {
      if (request.method !== "GET" && request.method !== "POST") return error(405, "method_not_allowed", "Use GET or POST.");
      return lookup(request, env);
    }
    // Static files are served before the Worker runs; reaching here means no file matched.
    const res = await env.ASSETS.fetch(request);
    if (res.status === 404) return error(404, "not_found", `No such resource: ${pathname}`);
    return res;
  },
};
