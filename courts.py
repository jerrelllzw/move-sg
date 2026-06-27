"""Fetch basketball courts in Singapore from OpenStreetMap (Overpass API).

No API key required. Output is written to data/courts.json, which is consumed
directly by the static web app. Re-run this script to refresh the data.

    python courts.py
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Every way/node/relation tagged as a basketball facility within Singapore.
OVERPASS_QUERY = """
[out:json][timeout:90];
area["name"="Singapore"]["admin_level"="2"]->.sg;
(
  nwr["sport"="basketball"](area.sg);
  nwr["leisure"="pitch"]["sport"~"basketball"](area.sg);
);
out center tags;
"""

OUTPUT_PATH = Path(__file__).parent / "data" / "courts.json"


def fetch_elements() -> list[dict[str, Any]]:
    """Query the Overpass API and return the raw OSM elements."""
    data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
    request = urllib.request.Request(
        OVERPASS_URL, data=data, headers={"User-Agent": "sg-courts/1.0"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    return payload.get("elements", [])


def element_coords(element: dict[str, Any]) -> tuple[float, float] | None:
    """Return (lat, lon) for a node, way, or relation, or None if missing."""
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    center = element.get("center")
    if center:
        return center["lat"], center["lon"]
    return None


def normalise(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert raw OSM elements into a compact, de-duplicated court list."""
    courts: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()

    for element in elements:
        coords = element_coords(element)
        if coords is None:
            continue

        # Round coordinates to ~11m to drop near-duplicate points.
        key = (round(coords[0], 4), round(coords[1], 4))
        if key in seen:
            continue
        seen.add(key)

        tags = element.get("tags", {})
        name = tags.get("name") or tags.get("operator") or "Basketball Court"
        courts.append(
            {
                "id": f"{element['type']}/{element['id']}",
                "name": name,
                "lat": round(coords[0], 6),
                "lng": round(coords[1], 6),
                "access": tags.get("access", ""),
                "surface": tags.get("surface", ""),
                "hoops": tags.get("hoops", ""),
                "lit": tags.get("lit", ""),
                "indoor": tags.get("indoor", ""),
            }
        )

    courts.sort(key=lambda c: c["name"].lower())
    return courts


def main() -> int:
    try:
        elements = fetch_elements()
    except Exception as error:  # noqa: BLE001 - surface any network/parse error
        print(f"Failed to fetch data from Overpass: {error}", file=sys.stderr)
        return 1

    courts = normalise(elements)
    if not courts:
        print("No courts found - aborting without overwriting data.", file=sys.stderr)
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(courts, file, ensure_ascii=False, indent=2)

    print(f"Saved {len(courts)} courts to {OUTPUT_PATH.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
