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

// The state, matched address, point and geographies of one geocoder answer, or null when nothing matched.
export function geographiesOf(result) {
  const match = result.addressMatches ? result.addressMatches[0] : null;
  const geos = match ? match.geographies : result.geographies;
  if (!geos || !geos.States || !geos.States[0]) return null;
  return {
    state: geos.States[0].STUSAB,
    matched_address: match ? match.matchedAddress : null,
    coordinates: match ? { lat: match.coordinates.y, lng: match.coordinates.x } : null,
    geos,
  };
}

// The first of `names` present in `geos`, as a district of layer `kind`.
export function pickDistrict(geos, kind, names) {
  for (const name of names) {
    const g = geos[name] && geos[name][0];
    if (g) return { layer: kind, geoid: g.GEOID, name: g.NAME, map: name };
  }
  return null;
}

// Districts on the maps the people in office were elected under. The congressional map is the one the build
// named (meta); each state legislative chamber names the maps it accepts (table.maps), and when the first
// answer has none of them, `again(vintage)` asks the geocoder once more with the chamber's own vintage.
export async function districtsFor(first, meta, table, again) {
  const districts = [];
  const cd = pickDistrict(first, "cd", [meta.congressional]);
  if (cd) districts.push(cd);
  for (const layer of ["sldu", "sldl"]) {
    const m = (table.maps || {})[layer];
    if (!m) continue;
    let d = pickDistrict(first, layer, m.accept);
    if (!d && m.geocoder_vintage) {
      const geos = await again(m.geocoder_vintage);
      d = geos ? pickDistrict(geos, layer, m.accept) : null;
    }
    if (d) districts.push(d);
  }
  return districts;
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

async function geocode(input, vintage) {
  const res = await fetch(geocoderUrl(input, vintage), { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`geocoder answered ${res.status}`);
  return (await res.json()).result;
}

async function lookup(request, env) {
  const input = await readInput(request);
  if (input.error) return error(400, "bad_request", input.error);

  const meta = await asset(env, request, "/v1/lookup/meta.json");
  if (!meta) return error(503, "not_built", "The lookup tables are missing from this deployment.");

  let found;
  try {
    found = geographiesOf(await geocode(input, meta.geocoder_vintage));
  } catch (e) {
    return error(502, "geocoder_unavailable", `The Census geocoder could not be reached (${e.message}).`);
  }
  if (!found) return error(404, "not_found", "The Census geocoder found no match for that location.");

  const table = await asset(env, request, `/v1/lookup/${found.state.toLowerCase()}.json`);
  if (!table) return error(404, "no_data", `No lookup table for ${found.state}.`);

  // A second geocoder call, at the point the first one matched, for a chamber on other maps.
  const point = found.coordinates || input;
  const seen = {};
  const again = async (vintage) => {
    if (!(vintage in seen)) {
      try {
        const g = geographiesOf(await geocode({ lat: point.lat, lng: point.lng }, vintage));
        seen[vintage] = g ? g.geos : null;
      } catch {
        seen[vintage] = null;
      }
    }
    return seen[vintage];
  };
  const districts = await districtsFor(found.geos, meta, table, again);
  const { officials, notes } = officialsFor({ districts }, table);
  for (const layer of ["sldu", "sldl"]) {
    if (table.maps && table.maps[layer] && !districts.some((d) => d.layer === layer)) {
      notes.push(`The ${layer === "sldu" ? "upper" : "lower"} chamber's district could not be read from the Census geocoder.`);
    }
  }
  return json(
    {
      state: found.state,
      matched_address: found.matched_address,
      coordinates: found.coordinates,
      districts,
      officials,
      notes,
      district_maps: { congress: meta.congress, congressional: meta.congressional },
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
