"""Build the swimming-pool layer from the official ActiveSG list of complexes.

The canonical list of public swimming complexes is hard-coded below (from
ActiveSG). Each name is geocoded with the Google Places API (New); the feature
name is pinned to the official list regardless of Google's own label.

Requires a Google Maps API key (.env / env var). The website only reads the
static data/pools.geojson and never touches Google.

    python pools.py
"""

import json
import sys
from pathlib import Path

from google_places import find_place, load_api_key, place_properties

# Official ActiveSG public swimming complexes. Each entry is the display name and
# an optional Google query (defaults to the name) for tricky lookups.
OFFICIAL_POOLS = [
    ("ActiveSG Sport Park @ Teck Ghee Swimming Complex", "Teck Ghee Swimming Complex"),
    ("Bishan Swimming Complex", None),
    ("Bukit Batok Swimming Complex", None),
    ("Bukit Canberra Swimming Complex", None),
    ("Choa Chu Kang Swimming Complex", None),
    ("Clementi Swimming Complex", None),
    ("Delta Swimming Complex", None),
    ("Geylang East Swimming Complex", None),
    ("Heartbeat @ Bedok ActiveSG Swimming Complex", "Heartbeat Bedok ActiveSG"),
    ("Hougang Swimming Complex", None),
    ("Jalan Besar Swimming Complex", None),
    ("Jurong East Swimming Complex", None),
    ("Jurong Lake Gardens Pool", "Jurong Lake Gardens swimming pool"),
    ("Jurong West Swimming Complex", None),
    ("Katong Swimming Complex", None),
    ("MOE (Evans) Swimming Complex", "Evans Swimming Complex"),
    ("Pasir Ris Swimming Complex", None),
    ("Queenstown Swimming Complex", None),
    ("Sengkang Swimming Complex", None),
    ("Senja-Cashew Swimming Complex", "Senja Cashew Swimming Complex"),
    ("Serangoon Swimming Complex", None),
    ("Tampines Swimming Complex", None),
    ("Woodlands Swimming Complex", None),
    ("Yio Chu Kang Swimming Complex", None),
    ("Yishun Swimming Complex", None),
]

SG_BBOX = (1.20, 103.60, 1.48, 104.05)
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "pools.geojson"


def resolve(name: str, query: str | None, api_key: str) -> dict | None:
    """Geocode one pool via Google. Returns a GeoJSON feature or None."""
    place = find_place(query or name, api_key, SG_BBOX)
    if not place:
        return None
    loc = place.get("location", {})
    return {
        "type": "Feature",
        # Pin the official ActiveSG name/source, but keep Google's rating + link.
        "properties": place_properties(place, name=name, source="Google"),
        "geometry": {
            "type": "Point",
            "coordinates": [round(loc.get("longitude", 0), 6), round(loc.get("latitude", 0), 6)],
        },
    }


def main() -> int:
    api_key = load_api_key()
    if not api_key:
        print("GOOGLE_MAPS_API_KEY not found. Set it in your environment or .env.", file=sys.stderr)
        return 1

    features = []
    unresolved = []
    for name, query in OFFICIAL_POOLS:
        try:
            feature = resolve(name, query, api_key)
        except Exception as error:  # noqa: BLE001
            print(f"Google Places request failed: {error}", file=sys.stderr)
            return 1
        if feature is None:
            unresolved.append(name)
            print(f"  ! could not geocode: {name}", file=sys.stderr)
            continue
        features.append(feature)
        print(f"  ok  {name}")

    if unresolved:
        print(f"Unresolved ({len(unresolved)}): {', '.join(unresolved)}", file=sys.stderr)
    if not features:
        print("No pools resolved - aborting without overwriting data.", file=sys.stderr)
        return 1

    features.sort(key=lambda f: f["properties"]["name"].lower())
    collection = {"type": "FeatureCollection", "features": features}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(collection, file, ensure_ascii=False, indent=2)

    print(f"Saved {len(features)}/{len(OFFICIAL_POOLS)} pools to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
