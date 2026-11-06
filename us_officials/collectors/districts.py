"""Census TIGER district tables: GEOID and name of every congressional and state legislative district.

The geocoder answers an address with GEOIDs; these tables tie each GEOID to the district names Open States
uses, so the lookup can go GEOID -> people without guessing at names at request time."""

from __future__ import annotations

from ..config import TIGERWEB_LEGISLATIVE
from ..http import Client

PUBLISHER = "U.S. Census Bureau TIGERweb"
_PAGE = 1000


def query_layer(client: Client, layer: int) -> list[dict]:
    """Every feature's GEOID, STATE (FIPS) and BASENAME in one TIGERweb layer, without geometry."""
    url = f"{TIGERWEB_LEGISLATIVE}/{layer}/query"
    rows: list[dict] = []
    offset = 0
    while True:
        data = client.get_json(
            url,
            params={
                "where": "1=1",
                "outFields": "GEOID,STATE,BASENAME",
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


def collect(client: Client, layers: dict[str, int]) -> dict[str, list[dict]]:
    """{"cd": [...], "sldu": [...], "sldl": [...]} for the given layer ids. Areas outside any district
    (GEOIDs ending in ZZ, "Districts not defined", mostly water) are dropped."""
    return {
        kind: [r for r in query_layer(client, layer) if not r["GEOID"].endswith("ZZ")] for kind, layer in layers.items()
    }
