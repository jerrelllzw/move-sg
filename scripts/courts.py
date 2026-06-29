"""Fetch basketball courts in Singapore from the Google Places API (New).

Requires a Google Maps API key with the "Places API (New)" enabled, set via the
GOOGLE_MAPS_API_KEY environment variable or a local .env file. The key is used
ONLY here, offline — the website reads the static data/courts.geojson and never
touches Google or your key.

    python courts.py

Cost note: this runs a text search across a grid of cells (≈ rows x cols x up to
3 paginated requests). Text Search is billed per request — shrink the grid to
spend less, grow it for better coverage.
"""

import json
import sys
from pathlib import Path

from google_places import load_api_key, search_grid, to_point_feature

# Singapore bounding box (south, west, north, east).
SG_BBOX = (1.20, 103.60, 1.48, 104.05)
GRID_ROWS = 5
GRID_COLS = 6

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "courts.geojson"


def main() -> int:
    api_key = load_api_key()
    if not api_key:
        print(
            "GOOGLE_MAPS_API_KEY not found. Set it in your environment or .env.",
            file=sys.stderr,
        )
        return 1

    try:
        places = search_grid(
            "basketball court", api_key, SG_BBOX, GRID_ROWS, GRID_COLS
        )
    except Exception as error:  # noqa: BLE001 - surface any network/parse error
        print(f"Google Places request failed: {error}", file=sys.stderr)
        return 1

    features = [to_point_feature(place) for place in places.values()]
    features.sort(key=lambda f: f["properties"]["name"].lower())
    if not features:
        print("No courts found - aborting without overwriting data.", file=sys.stderr)
        return 1

    collection = {"type": "FeatureCollection", "features": features}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(collection, file, ensure_ascii=False)

    print(f"Saved {len(features)} courts to {OUTPUT_PATH.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
