"""Fetch Singapore's Park Connector Network from data.gov.sg (NParks, official).

The line geometry comes from data.gov.sg (no key needed). The dataset splits each
connector into many segments that share one PARK name, and none carry a Google
place. To make a connector open to a single canonical pin on Google Maps, every
*unique name* is geocoded once against the Google Places API (New) — biased to the
combined centre of its segments so a same-named place elsewhere doesn't win — and a
confident match's google_maps_uri (plus rating/reviews/address) is copied onto all
segments with that name. The line geometry and official name are kept as-is.

Geocoding needs a Google Maps API key (GOOGLE_MAPS_API_KEY env var or .env). If no
key is present the connectors are still written, just without Google links. The
website only reads the static data/pcn.geojson and never touches Google.

    python pcn.py

Cost note: one Text Search request per unique name (~160), not per segment.
Results are cached to data/pcn_places.json keyed by name, so a re-run only geocodes
names not already in the cache (delete the cache to force a full refresh). The run
is resumable — the cache is checkpointed as it goes.
"""

import json
import math
import re
import sys
import time
from pathlib import Path

import utf8_console  # noqa: F401  — forces UTF-8 stdout/stderr (Windows safety)
from datagovsg import download_geojson
from google_places import find_place_near, load_api_key

# NParks "Park Connector Loop" dataset.
DATASET_ID = "d_a69ef89737379f231d2ae93fd1c5707f"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "pcn.geojson"
# Name -> resolved Google props (or None when no confident match), so re-runs are
# free and resumable. Delete this file to re-geocode every name from scratch.
CACHE_PATH = DATA_DIR / "pcn_places.json"

# Connectors are long lines, so their centre can sit well away from where Google
# labels the place — looser than the parks thresholds to compensate, but still
# tight enough to reject a coincidental same-named place across the island.
BIAS_RADIUS_M = 3000
MAX_MATCH_DIST_M = 5000


def transform(geojson: dict) -> dict:
    """Keep the line geometry, trim to the name + loop we display."""
    features = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        props = feature.get("properties", {})
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": props.get("PARK") or "Park Connector",
                    "loop": props.get("PCN_LOOP", ""),
                },
                "geometry": geometry,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def search_query(name: str) -> str:
    """A clean place query from a segment name.

    Drops the parenthetical segment descriptor ("(opp bank)", "(KJE - ...)"),
    collapses stray whitespace/newlines from dirty source rows, and expands the
    "PC" abbreviation to "Park Connector" so Google has a real place name to match.
    """
    query = re.sub(r"\([^)]*\)", " ", name.splitlines()[0])
    return " ".join("Park Connector" if word == "PC" else word for word in query.split())


def normalize(name: str) -> str:
    """Lower-cased comparison key: same cleaning as search_query, case-folded.

    Lets us tell a genuine 1:1 hit ("Bedok PC" -> "Bedok Park Connector") from a
    nearest-but-wrong place Google returned for an unknown connector.
    """
    return search_query(name).casefold()


# Words shared by most connector names — they don't identify *which* connector, so
# they're ignored when checking that the place is the same one.
GENERIC_TOKENS = {
    "park", "connector", "pc", "pcn", "the", "of", "to", "loop", "trail", "head",
    "nature", "corridor", "walk", "promenade", "linear", "garden", "gardens",
}
# Map local abbreviations to their full form so "Lor"/"Lorong", "Ave"/"Avenue"
# etc. count as the same identifying token on either side of the comparison.
TOKEN_ALIASES = {
    "ave": "avenue", "st": "street", "rd": "road", "dr": "drive", "res": "reservoir",
    "lor": "lorong", "sg": "sungei", "jln": "jalan", "bt": "bukit", "upp": "upper",
}


def key_tokens(text: str) -> set[str]:
    """The identifying tokens of a name: alphanumerics, de-abbreviated, sans filler."""
    tokens = set()
    for raw in re.findall(r"[a-z0-9]+", text.casefold()):
        tokens.update(TOKEN_ALIASES.get(raw, raw).split())
    return tokens - GENERIC_TOKENS


def _positions(node):
    """Yield every [lng, lat, ...] coordinate pair nested in a geometry."""
    if node and isinstance(node[0], (int, float)):
        yield node
        return
    for child in node:
        yield from _positions(child)


def centroid(geometries: list[dict]) -> tuple[float, float]:
    """Approximate (lat, lng) centre across one or more geometries."""
    points = [p for geom in geometries for p in _positions(geom["coordinates"])]
    lng = sum(p[0] for p in points) / len(points)
    lat = sum(p[1] for p in points) / len(points)
    return lat, lng


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def place_name(place: dict) -> str:
    return place.get("displayName", {}).get("text", "")


def is_connector(name: str) -> bool:
    """Whether a row names a connector (vs. a plain park/place in the same dataset)."""
    lowered = normalize(name)
    return "connector" in lowered or "pcn" in lowered


def park_query(name: str) -> str:
    """Query for a park row: the clean name minus any trailing zone number.

    The dataset splits some parks into numbered zones ("West Coast Park 1/2/3",
    "Pasir Ris Park 1-5"); Google knows only the whole park, so the zone number is
    dropped to find it.
    """
    return re.sub(r"\s+\d+$", "", search_query(name)).strip()


def place_query(name: str) -> str:
    return search_query(name) if is_connector(name) else park_query(name)


def is_connector_match(name: str, google_name: str) -> bool:
    """Keep a match when Google's place plausibly is this connector.

    Either gate suffices. Gate 1: the Google name equals the connector name once
    our "PC" abbreviation and "(...)" descriptor are normalised away — an exact
    hit, accepted as-is. Gate 2 is the fallback when the names differ: accept only
    if the Google name reads as a connector ("connector"/"pcn") *and* carries every
    identifying token of the connector — so "Choa Chu Kang" can't bind to "Stagmont
    Park Connector", nor "Woodlands Ave 5" to "Woodlands (Ave 7)", while pure naming
    variants still pass.
    """
    if normalize(google_name) == normalize(name):
        return True
    lowered = google_name.casefold()
    if "connector" not in lowered and "pcn" not in lowered:
        return False
    return key_tokens(search_query(name)).issubset(key_tokens(google_name))


def is_park_match(name: str, google_name: str) -> bool:
    """Keep a match when Google's place is this park — no "connector" required.

    The park itself is the target, so a match just needs every identifying token of
    the (zone-number-stripped) park name to appear in the Google name. Google
    carrying extra words is fine ("West Coast Park 1" -> "West Coast Park").
    """
    needed = key_tokens(park_query(name))
    return bool(needed) and needed.issubset(key_tokens(google_name))


def is_match(name: str, google_name: str) -> bool:
    """Route a candidate to the connector or park test based on the row's own name."""
    if is_connector(name):
        return is_connector_match(name, google_name)
    return is_park_match(name, google_name)


def geocode(name: str, geometries: list[dict], api_key: str) -> dict | None:
    """Find the Google place for a row near its centre; return props or None.

    Connectors match strictly (1:1 name/token); parks match on their own name with
    no "connector" requirement. Of the accepted candidates, the nearest within
    MAX_MATCH_DIST_M wins, so a same-named place elsewhere is rejected.
    """
    clat, clng = centroid(geometries)
    needed = key_tokens(place_query(name))
    best, best_dist, best_rank = None, None, None
    for place in find_place_near(place_query(name), api_key, clat, clng, BIAS_RADIUS_M):
        if not is_match(name, place_name(place)):
            continue
        loc = place.get("location", {})
        plat, plng = loc.get("latitude"), loc.get("longitude")
        if plat is None or plng is None:
            continue
        dist = haversine_m(clat, clng, plat, plng)
        if dist > MAX_MATCH_DIST_M:
            continue
        # Prefer the barest name (the park/connector itself) over a sub-POI that
        # merely contains its name ("West Coast Park" beats "…Car Park 3"), then
        # break ties by proximity.
        rank = (len(key_tokens(place_name(place)) - needed), dist)
        if best_rank is None or rank < best_rank:
            best, best_dist, best_rank = place, dist, rank

    if best is None:
        print(f"  --  {name}  (no confident match)")
        return None

    # Underscore-prefixed keys are an audit trail kept in the cache only; enrich()
    # strips them before they reach the geojson properties.
    props = {"_google_name": place_name(best), "_dist_m": round(best_dist)}
    if best.get("googleMapsUri"):
        props["google_maps_uri"] = best["googleMapsUri"]
    if best.get("rating") is not None:
        props["rating"] = best["rating"]
    if best.get("userRatingCount") is not None:
        props["reviews"] = best["userRatingCount"]
    if best.get("formattedAddress"):
        props["address"] = best["formattedAddress"]
    print(f"  ok  {name}  ->  {place_name(best)}  ({best_dist:.0f}m)")
    return props


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def enrich(features: list[dict], api_key: str) -> None:
    """Geocode each unique name once and copy the link onto all its segments."""
    by_name: dict[str, list[dict]] = {}
    for feature in features:
        by_name.setdefault(feature["properties"]["name"], []).append(feature)

    cache = load_cache()
    looked_up = 0
    try:
        for name, group in by_name.items():
            if name not in cache:
                cache[name] = geocode(name, [f["geometry"] for f in group], api_key)
                looked_up += 1
                if looked_up % 20 == 0:  # checkpoint so a crash keeps progress
                    save_cache(cache)
                time.sleep(0.1)  # be gentle on the API
            props = cache.get(name)
            if props:
                public = {k: v for k, v in props.items() if not k.startswith("_")}
                for feature in group:
                    feature["properties"].update(public)
    finally:
        save_cache(cache)

    linked_names = sum(1 for name in by_name if cache.get(name))
    print(
        f"Linked {linked_names}/{len(by_name)} connector names to a Google place "
        f"({looked_up} new lookups)"
    )


def main() -> int:
    try:
        geojson = download_geojson(DATASET_ID)
    except Exception as error:  # noqa: BLE001 - surface any network/parse error
        print(f"Failed to fetch data from data.gov.sg: {error}", file=sys.stderr)
        return 1

    collection = transform(geojson)
    if not collection["features"]:
        print("No connectors found - aborting without overwriting data.", file=sys.stderr)
        return 1

    api_key = load_api_key()
    if api_key:
        enrich(collection["features"], api_key)
    else:
        print(
            "GOOGLE_MAPS_API_KEY not found - saving connectors without Google links.",
            file=sys.stderr,
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(collection, file, ensure_ascii=False)

    print(f"Saved {len(collection['features'])} connector segments to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
