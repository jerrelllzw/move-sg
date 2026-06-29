"""Fetch Singapore's Park Connector Network from data.gov.sg (NParks, official).

No API key required. Output is written to data/pcn.geojson, consumed as a
toggleable overlay by the static web app.

    python pcn.py
"""

import json
import sys
from pathlib import Path

from datagovsg import download_geojson

# NParks "Park Connector Loop" dataset.
DATASET_ID = "d_a69ef89737379f231d2ae93fd1c5707f"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "pcn.geojson"


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

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(collection, file, ensure_ascii=False)

    print(
        f"Saved {len(collection['features'])} connector segments "
        f"to {OUTPUT_PATH.relative_to(Path.cwd())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
