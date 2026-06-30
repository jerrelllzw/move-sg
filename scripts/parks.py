"""Fetch parks in Singapore from data.gov.sg (NParks, official).

The park polygons come from data.gov.sg (no key needed). To make each park open
to a single canonical pin on Google Maps — instead of a name search that lands on
a few candidates — every park is then geocoded against the Google Places API
(New): its name, biased to the park's own location so a same-named place
elsewhere doesn't win. A confident match contributes a `google_maps_uri` (plus
rating/reviews/address) to the feature; the polygon geometry and official name
are kept as-is.

Geocoding needs a Google Maps API key (GOOGLE_MAPS_API_KEY env var or .env). If
no key is present the parks are still written, just without Google links (the app
falls back to a name search for those). The website only reads the static
data/parks.geojson and never touches Google.

    python parks.py

Cost note: one Text Search request per park (~hundreds). Results are cached to
data/parks_places.json keyed by name, so a re-run only geocodes parks not already
in the cache (delete the cache to force a full refresh). The run is resumable —
the cache is checkpointed as it goes.
"""

import json
import math
import sys
import time
from pathlib import Path

import utf8_console  # noqa: F401  — forces UTF-8 stdout/stderr (Windows safety)
from datagovsg import download_geojson
from google_places import find_place_near, load_api_key

# NParks "Parks and Nature Reserves" dataset (managed-area polygons).
DATASET_ID = "d_77d7ec97be83d44f61b85454f844382f"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "parks.geojson"
# Name -> resolved Google props (or None when no confident match), so re-runs are
# free and resumable. Delete this file to re-geocode every park from scratch.
CACHE_PATH = DATA_DIR / "parks_places.json"

# How far Google's pin may sit from the park's centroid and still count as the
# same park. Generous because a long park (East Coast, the connectors) puts its
# centroid far from where Google labels it, but tight enough to reject a match on
# the other side of the island.
BIAS_RADIUS_M = 2000
MAX_MATCH_DIST_M = 2500

# The official NAME field is upper-case with abbreviations; expand for display.
ABBREVIATIONS = {
    "PG": "Playground",
    "PK": "Park",
    "GDN": "Garden",
    "GDNS": "Gardens",
    "RD": "Road",
    "AVE": "Avenue",
    "ST": "Street",
    "CTR": "Centre",
    "NR": "Nature Reserve",
    "PL": "Place",
}


def clean_name(raw: str) -> str:
    if not raw:
        return "Park"
    return " ".join(ABBREVIATIONS.get(word, word.capitalize()) for word in raw.split())


def transform(geojson: dict) -> dict:
    """Keep the polygon geometry, attach a tidied name."""
    features = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        props = feature.get("properties", {})
        features.append(
            {
                "type": "Feature",
                "properties": {"name": clean_name(props.get("NAME", ""))},
                "geometry": geometry,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _positions(node):
    """Yield every [lng, lat, ...] coordinate pair nested in a geometry."""
    if node and isinstance(node[0], (int, float)):
        yield node
        return
    for child in node:
        yield from _positions(child)


def centroid(geometry: dict) -> tuple[float, float]:
    """Approximate (lat, lng) centre of a polygon as the mean of its vertices."""
    points = list(_positions(geometry["coordinates"]))
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


def geocode(name: str, geometry: dict, api_key: str) -> dict | None:
    """Find the Google place for a park near its centroid; return props or None.

    Picks the nearest returned candidate within MAX_MATCH_DIST_M of the centroid
    so a coincidental same-named place across the island is rejected.
    """
    clat, clng = centroid(geometry)
    best, best_dist = None, MAX_MATCH_DIST_M
    for place in find_place_near(name, api_key, clat, clng, BIAS_RADIUS_M):
        loc = place.get("location", {})
        plat, plng = loc.get("latitude"), loc.get("longitude")
        if plat is None or plng is None:
            continue
        dist = haversine_m(clat, clng, plat, plng)
        if dist <= best_dist:
            best, best_dist = place, dist

    if best is None:
        print(f"  --  {name}  (no confident match)")
        return None

    props = {}
    if best.get("googleMapsUri"):
        props["google_maps_uri"] = best["googleMapsUri"]
    if best.get("rating") is not None:
        props["rating"] = best["rating"]
    if best.get("userRatingCount") is not None:
        props["reviews"] = best["userRatingCount"]
    if best.get("formattedAddress"):
        props["address"] = best["formattedAddress"]
    print(f"  ok  {name}  ({best_dist:.0f}m)")
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
    """Attach Google links to each park in place, using/refreshing the cache."""
    cache = load_cache()
    looked_up = 0
    try:
        for feature in features:
            name = feature["properties"]["name"]
            if name not in cache:
                cache[name] = geocode(name, feature["geometry"], api_key)
                looked_up += 1
                if looked_up % 20 == 0:  # checkpoint so a crash keeps progress
                    save_cache(cache)
                time.sleep(0.1)  # be gentle on the API
            props = cache.get(name)
            if props:
                feature["properties"].update(props)
    finally:
        save_cache(cache)

    linked = sum(1 for f in features if f["properties"].get("google_maps_uri"))
    print(f"Linked {linked}/{len(features)} parks to a Google place ({looked_up} new lookups)")


def main() -> int:
    try:
        geojson = download_geojson(DATASET_ID)
    except Exception as error:  # noqa: BLE001 - surface any network/parse error
        print(f"Failed to fetch data from data.gov.sg: {error}", file=sys.stderr)
        return 1

    collection = transform(geojson)
    if not collection["features"]:
        print("No parks found - aborting without overwriting data.", file=sys.stderr)
        return 1

    api_key = load_api_key()
    if api_key:
        enrich(collection["features"], api_key)
    else:
        print(
            "GOOGLE_MAPS_API_KEY not found - saving parks without Google links.",
            file=sys.stderr,
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(collection, file, ensure_ascii=False)

    print(f"Saved {len(collection['features'])} parks to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
