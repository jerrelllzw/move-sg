"""Fetch parks in Singapore from data.gov.sg (NParks, official).

No API key required. Output is written to data/parks.geojson as polygons,
consumed as a toggleable overlay by the static web app.

    python parks.py
"""

import json
import sys
from pathlib import Path

from datagovsg import download_geojson

# NParks "Parks and Nature Reserves" dataset (managed-area polygons).
DATASET_ID = "d_77d7ec97be83d44f61b85454f844382f"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "parks.geojson"

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

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(collection, file, ensure_ascii=False)

    print(
        f"Saved {len(collection['features'])} parks "
        f"to {OUTPUT_PATH.relative_to(Path.cwd())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
