"""Helper for the Google Places API (New) Text Search.

Used by the data-collection scripts only — never by the website. Reads the key
from the GOOGLE_MAPS_API_KEY environment variable or a local .env file (which is
git-ignored, so the key is never committed or shipped to the browser).

Note on cost: Text Search is billed per request. Each grid cell is 1-3 requests
(pagination), so a finer grid means more coverage but a larger bill. Tune the
rows/cols in courts.py / pools.py to taste.
"""

import json
import os
import time
import urllib.request
from pathlib import Path

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.location,"
    "places.formattedAddress,places.rating,places.userRatingCount,"
    "places.googleMapsUri,places.businessStatus,nextPageToken"
)


def load_api_key() -> str | None:
    """Return the Google Maps API key from the environment or .env."""
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if key:
        return key.strip()
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GOOGLE_MAPS_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return None


def _post(body: dict, api_key: str) -> dict:
    """POST one searchText request and return the decoded JSON."""
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def _search_page(query: str, api_key: str, rect: tuple, token: str | None) -> dict:
    body = {
        "textQuery": query,
        "locationRestriction": {
            "rectangle": {
                "low": {"latitude": rect[0], "longitude": rect[1]},
                "high": {"latitude": rect[2], "longitude": rect[3]},
            }
        },
        "pageSize": 20,
    }
    if token:
        body["pageToken"] = token
    return _post(body, api_key)


def _search_rect(query: str, api_key: str, rect: tuple) -> dict:
    """All places matching `query` inside one rectangle, following pagination."""
    places = {}
    token = None
    while True:
        data = _search_page(query, api_key, rect, token)
        for place in data.get("places", []):
            places[place["id"]] = place
        token = data.get("nextPageToken")
        if not token:
            break
        time.sleep(2)  # nextPageToken needs a moment to become valid
    return places


def grid(bbox: tuple, rows: int, cols: int) -> list[tuple]:
    """Split a (south, west, north, east) bbox into rows x cols rectangles."""
    south, west, north, east = bbox
    dlat = (north - south) / rows
    dlng = (east - west) / cols
    return [
        (south + i * dlat, west + j * dlng, south + (i + 1) * dlat, west + (j + 1) * dlng)
        for i in range(rows)
        for j in range(cols)
    ]


def search_grid(query: str, api_key: str, bbox: tuple, rows: int, cols: int) -> dict:
    """Run a text search across a grid of cells, de-duplicated by place id."""
    places = {}
    cells = grid(bbox, rows, cols)
    for index, rect in enumerate(cells, 1):
        places.update(_search_rect(query, api_key, rect))
        print(f"  cell {index}/{len(cells)} -> {len(places)} unique so far")
        time.sleep(0.2)
    return places


def find_place(query: str, api_key: str, bbox: tuple) -> dict | None:
    """Return the single best Google match for a query within the bbox."""
    data = _search_page(query, api_key, bbox, None)
    places = data.get("places", [])
    return places[0] if places else None


def find_place_near(
    query: str, api_key: str, lat: float, lng: float, radius_m: float = 2000
) -> list[dict]:
    """Up to a few Google matches for a query, ranked toward (lat, lng).

    Uses a location *bias* (soft) rather than a restriction (hard) so a place
    whose pin sits just outside the circle still surfaces; callers can gate the
    results on distance themselves.
    """
    body = {
        "textQuery": query,
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius_m,
            }
        },
        "pageSize": 5,
    }
    return _post(body, api_key).get("places", [])


def place_properties(place: dict, **extra) -> dict:
    """Common GeoJSON properties for a Google place: name, address, rating, link.

    Rating fields are only included when Google returns them, so unrated places
    stay lean. `extra` lets callers pin a name/source on top.
    """
    props = {
        "name": place.get("displayName", {}).get("text", "Unnamed"),
        "address": place.get("formattedAddress", ""),
    }
    if place.get("rating") is not None:
        props["rating"] = place["rating"]
    if place.get("userRatingCount") is not None:
        props["reviews"] = place["userRatingCount"]
    if place.get("googleMapsUri"):
        props["google_maps_uri"] = place["googleMapsUri"]
    if place.get("businessStatus"):
        props["status"] = place["businessStatus"]
    props.update(extra)
    return props


def to_point_feature(place: dict) -> dict:
    """Convert a Google place into a GeoJSON point feature."""
    loc = place.get("location", {})
    return {
        "type": "Feature",
        "properties": place_properties(place),
        "geometry": {
            "type": "Point",
            "coordinates": [
                round(loc.get("longitude", 0), 6),
                round(loc.get("latitude", 0), 6),
            ],
        },
    }
