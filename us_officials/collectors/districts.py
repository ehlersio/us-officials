"""Census TIGER district tables: GEOID, name and land area of every congressional and state legislative district.

The geocoder answers an address with GEOIDs; these tables tie each GEOID to the district names Open States
uses, so the lookup can go GEOID -> people without guessing at names at request time. Land area is compared
between two maps to tell which states' districts actually differ."""

from __future__ import annotations

from ..config import TIGERWEB_LEGISLATIVE
from ..http import Client

PUBLISHER = "U.S. Census Bureau TIGERweb"
_PAGE = 1000


def layer_ids(client: Client) -> dict[str, int]:
    """TIGERweb Legislative layer name -> id. Some names appear twice (the same map under two vintages);
    the first id is kept."""
    ids: dict[str, int] = {}
    for layer in client.get_json(TIGERWEB_LEGISLATIVE, params={"f": "json"}).get("layers") or []:
        ids.setdefault(layer["name"], layer["id"])
    return ids


def query_layer(client: Client, layer: int) -> list[dict]:
    """Every feature's GEOID, STATE (FIPS), BASENAME and AREALAND in one TIGERweb layer, without geometry."""
    url = f"{TIGERWEB_LEGISLATIVE}/{layer}/query"
    rows: list[dict] = []
    offset = 0
    while True:
        data = client.get_json(
            url,
            params={
                "where": "1=1",
                "outFields": "GEOID,STATE,BASENAME,AREALAND",
                "returnGeometry": "false",
                "orderByFields": "GEOID",
                "resultOffset": offset,
                "resultRecordCount": _PAGE,
                "f": "json",
            },
        )
        if "error" in data:
            raise RuntimeError(f"TIGERweb layer {layer}: {data['error']}")
        feats = data.get("features") or []
        rows += [f["attributes"] for f in feats]
        if not data.get("exceededTransferLimit") and len(feats) < _PAGE:
            return rows
        offset += len(feats)


def collect(client: Client, names: list[str]) -> dict[str, list[dict]]:
    """{layer name: rows} for the given layer names. Areas outside any district (GEOIDs ending in ZZ,
    "Districts not defined", mostly water) are dropped."""
    ids = layer_ids(client)
    missing = [n for n in names if n not in ids]
    if missing:
        raise SystemExit(f"TIGERweb has no layer named {missing}; available: {sorted(ids)}")
    return {n: [r for r in query_layer(client, ids[n]) if not r["GEOID"].endswith("ZZ")] for n in names}
