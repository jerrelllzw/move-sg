"""Fetch Singapore basketball courts from Google (legacy Places Nearby Search).

Nearby Search with keyword="basketball court" matches the term against everything
Google has indexed for a place — name, type, address AND customer reviews — then
ranks by prominence. That review-matching is what surfaces public HDB / void-deck
courts that aren't formally named "basketball court", which the newer Text Search
API does not do. Each result already carries its rating and review count.

Requires a Google Maps API key with the legacy Places API enabled
(GOOGLE_MAPS_API_KEY env var or .env). The website only reads the static
data/courts.geojson and never touches Google.

    python courts.py               # live: call Google, cache raw, filter, save
    python courts.py --from-cache  # offline: re-filter the cached raw results
    python courts.py --no-filter   # keep every result (skip the condo filter)

A live run caches every raw result to data/courts_raw.json, so after tweaking
KEEP_PATTERNS you can re-run with --from-cache for free (no API calls). The
filtered-out entries are also written to data/courts_dropped.geojson for review.

Cost note: coverage is an adaptive grid of circles (see SG_BBOX / BASE_RADIUS_M).
Each circle is 1-3 billed requests, and dense areas that hit the 60-result cap
get subdivided into more circles — so the bill scales with court density, not a
fixed point count. --from-cache makes no requests.
"""

import json
import math
import re
import sys

import utf8_console  # noqa: F401  — forces UTF-8 stdout/stderr (Windows safety)
import time
import urllib.parse
import urllib.request
from pathlib import Path

from google_places import load_api_key

NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
KEYWORD = "basketball court"

# --- Adaptive search grid ----------------------------------------------------
# Nearby Search returns at most 60 results per query (3 pages x 20). The keyword
# matches reviews too, so condos/playgrounds eat result slots — a big circle over
# a dense estate caps out and silently drops real courts. So we cover Singapore
# with overlapping circles and, whenever a circle hits the 60-cap, split it into
# four half-radius circles and re-query — spending extra requests only where the
# density actually needs them.
SG_BBOX = (1.21, 103.61, 1.47, 104.06)  # south, west, north, east
BASE_RADIUS_M = 3000
# Spacing <= radius*sqrt(2) so each circle's inscribed square tiles with no gaps.
BASE_SPACING_M = 4000
MIN_RADIUS_M = 500       # stop subdividing once circles get this small
PAGE_CAP = 60            # a full 60 results means the query was probably truncated

# The keyword search also matches *reviews*, so two kinds of non-court surface:
#   - private condos (residents mention "basketball" in reviews), and
#   - plain HDB blocks / playgrounds that sit next to a court — the real court is
#     already listed as its own entry, so the block is a phantom duplicate.
# Neither can be told apart from a court by Google's place `types` (all just
# point_of_interest), so we keep-list instead: a result stays only if its NAME
# looks like a court or a public facility that may contain one (community club,
# park, school, sport centre...). Everything else — condos, bare block/playground
# names — is dropped.
KEEP_PATTERNS = [
    # Court-like ("MPC" = multi-purpose court, a free public basketball court)
    r"basketball", r"hardcourt", r"streetball", r"\bbball\b", r"\bhoops?\b",
    r"\bcourt\b", r"\bmpc\b", r"shot zone", r"street court",
    # Public facilities that may house a court
    r"community club", r"community cent(?:re|er)", r"\bcc\b", r"\brc\b",
    r"residents", r"recreation", r"clubhouse", r"\bsports?\b",
    r"\bstadium\b", r"\barena\b", r"active\s?sg",
    r"\bschool\b", r"\bcollege\b", r"polytechnic", r"university",
    r"institute", r"academy", r"\bhub\b", r"\bpark\b",
]
KEEP_RE = [re.compile(p, re.I) for p in KEEP_PATTERNS]

# The search grid's north edge spills across the Strait of Johor into Johor
# Bahru, and its west edge into Iskandar Puteri / Gelang Patah, so Malaysian
# courts surface. They can't be told apart by latitude alone (Forest City sits
# at the same latitude as Singapore, just west of the strait), so we match the
# address against Johor district names. Generic Malay words like "jalan"/"taman"
# also appear in Singapore addresses, so those are deliberately excluded.
MALAYSIA_PATTERNS = [
    r"\bjohor\b", r"gelang patah", r"iskandar puteri", r"forest city",
    r"puteri harbour", r"\bmedini\b", r"\bmasai\b",
]
MALAYSIA_RE = [re.compile(p, re.I) for p in MALAYSIA_PATTERNS]


def in_malaysia(place: dict) -> bool:
    """True if the address/name points to Johor, Malaysia rather than Singapore."""
    text = f"{place.get('name', '')} {place.get('vicinity', '')}"
    return any(pattern.search(text) for pattern in MALAYSIA_RE)


# The keyword search also surfaces single-sport courts for other sports (their
# names still contain "court"/"hub", so KEEP_PATTERNS would let them through).
# This is a basketball map, so drop futsal/badminton/tennis/volleyball — unless
# the name also says "basketball" (a multi-sport hardcourt that includes one).
OTHER_SPORT_RE = re.compile(r"\b(?:futsal|badminton|tennis|volleyball)\b", re.I)


def is_other_sport(place: dict) -> bool:
    """True for a court dedicated to a non-basketball sport."""
    name = place.get("name", "")
    if re.search(r"\bbasketball\b", name, re.I):
        return False
    return bool(OTHER_SPORT_RE.search(name))


# Manually reviewed duplicates: a generic venue centroid (a park, community club,
# stadium...) that names the same place as a more specific basketball-court entry,
# leaving two pins for one spot. These are human de-dup decisions, not something a
# name rule captures cleanly, so we exclude them explicitly by place_id (stable
# even if Google renames the place). Keep this list in sync if duplicates resurface.
EXCLUDED_PLACE_IDS = {
    # Generic venue pins that duplicate a basketball-court entry.
    "ChIJmZrv0hsY2jERoh0_KEsvnlY",  # Shot Zone
    "ChIJq6qqmoIZ2jER48d5nbUw7hA",  # Kebun Baru Community Club
    "ChIJ_3Bj3IQa2jERM0aLDH-DUqA",  # Firefly Park
    "ChIJu0xj3EMU2jERFYVk0DZT7Kg",  # Nee Soon Link Park
    "ChIJ_6ftJ_ER2jERjyRotGhn7to",  # Yew Tee Park
    "ChIJOa5WOTIY2jER3vTbKwtyREo",  # Geylang West Community Club
    "ChIJtz1k35c92jERvtuLIIDzCVI",  # Brontosaur Park
    "ChIJ4YgAeX4T2jERVlOBy85Z-Wc",  # Canberra Park
    "ChIJNZ8HHC492jER08Oay4alGbk",  # Park Aquaria
    "ChIJQ6n9Ygw92jERM5o8IkMEhyU",  # Sun Plaza Park
    "ChIJ50JKpe0R2jER7-GTEwN3rG8",  # Choa Chu Kang Stadium
    "ChIJna83rrA92jERGxukdgLRHlI",  # Pasir Ris Park
    # Reviewed near-duplicate venues (kept the more specific sibling instead).
    "ChIJTV3-1LwX2jER_FUr4igueLo",  # ActiveSG Gym @ Ang Mo Kio Community Centre
    "ChIJdZqloOcW2jERfOq0MTBjb20",  # ActiveSG Sport Park
    "ChIJ0_0XGEkY2jER-GDi236NmsA",  # OCBC Arena Hall 1
    "ChIJO9HYGkkY2jERF88ndZmK10o",  # OCBC Arena
    "ChIJk4zKXXwZ2jERYa-98NwDuJU",  # Membina Court Residents' Committee
    "ChIJFUQbXgAZ2jERNgLwZbWUDXM",  # Membina Court
    "ChIJ-ZbQSvIR2jERzhg_o4EFHGo",  # Stagmont Park Residents' Committee
    "ChIJB1ziZ_IR2jERz1YXFIkUifo",  # Stagmont Park
    "ChIJfRlYVWwZ2jERSy_8N9cwJZU",  # Duxton Plain Park Calisthenics Fitness Corner
    "ChIJKYNzcHIZ2jERAyCQV-Iajbo",  # Duxton Plain Park
}


def keep_place(place: dict) -> bool:
    """Keep Singapore basketball courts and public facilities; drop Malaysian
    results, other-sport courts, reviewed duplicates, condos, and other noise."""
    if place.get("place_id") in EXCLUDED_PLACE_IDS:
        return False
    if in_malaysia(place):
        return False
    if is_other_sport(place):
        return False
    if "park" in place.get("types", []):
        return True
    name = place.get("name", "")
    return any(pattern.search(name) for pattern in KEEP_RE)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "courts.geojson"
# Every unique raw Nearby Search result, so the filter can be re-run offline
# (python courts.py --from-cache) after tweaking KEEP_PATTERNS — no re-billing.
RAW_CACHE_PATH = DATA_DIR / "courts_raw.json"
# What the filter removed, for manual review (names + coords + ratings).
DROPPED_PATH = DATA_DIR / "courts_dropped.geojson"


def _get(params: dict) -> dict:
    url = NEARBY_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode())


def search_point(lat: float, lng: float, radius_m: float, api_key: str) -> list[dict]:
    """All Nearby Search results around one point, following pagination."""
    results: list[dict] = []
    params = {
        "location": f"{lat},{lng}",
        "radius": int(radius_m),
        "keyword": KEYWORD,
        "key": api_key,
    }
    while True:
        data = _get(params)
        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            raise RuntimeError(f"{status}: {data.get('error_message', '')}")
        results.extend(data.get("results", []))
        token = data.get("next_page_token")
        if not token:
            break
        time.sleep(2)  # next_page_token needs a moment to become valid
        params = {"pagetoken": token, "key": api_key}
    return results


def _offset_deg(dlat_m: float, dlng_m: float, lat: float) -> tuple[float, float]:
    """Convert a north/east metre offset to (dlat, dlng) degrees at this latitude."""
    dlat = dlat_m / 111320.0
    dlng = dlng_m / (111320.0 * math.cos(math.radians(lat)))
    return dlat, dlng


def base_grid(bbox: tuple, spacing_m: float) -> list[tuple]:
    """Regular lat/lng grid of circle centres covering the bbox."""
    south, west, north, east = bbox
    dlat, _ = _offset_deg(spacing_m, 0, (south + north) / 2)
    points, lat = [], south
    while lat <= north:
        _, dlng = _offset_deg(0, spacing_m, lat)
        lng = west
        while lng <= east:
            points.append((lat, lng))
            lng += dlng
        lat += dlat
    return points


def search_adaptive(api_key: str) -> dict[str, dict]:
    """Search the base grid, recursively splitting any circle that hits the cap.

    A capped circle is split into four half-radius circles offset by radius*√2/4
    (so the four inscribed squares tile the parent's), down to MIN_RADIUS_M.
    """
    unique: dict[str, dict] = {}
    # (lat, lng, radius); start from the coarse base grid.
    stack = [(lat, lng, float(BASE_RADIUS_M)) for lat, lng in base_grid(SG_BBOX, BASE_SPACING_M)]
    done = 0
    while stack:
        lat, lng, radius = stack.pop()
        results = search_point(lat, lng, radius, api_key)
        done += 1
        for place in results:
            unique[place["place_id"]] = place

        note = ""
        if len(results) >= PAGE_CAP and radius > MIN_RADIUS_M:
            half = radius / 2
            off = radius * math.sqrt(2) / 4
            for slat in (-1, 1):
                for slng in (-1, 1):
                    dlat, dlng = _offset_deg(off * slat, off * slng, lat)
                    stack.append((lat + dlat, lng + dlng, half))
            note = f"  ⚠ hit {PAGE_CAP}-cap -> split into 4 @ {half:.0f}m"
        elif len(results) >= PAGE_CAP:
            note = f"  ⚠ capped at min radius {radius:.0f}m (may still miss some)"

        print(
            f"  [{done}] {lat:.4f},{lng:.4f} r={radius:.0f}m -> "
            f"{len(results)} results, {len(unique)} unique{note}"
        )
    return unique


def to_feature(place: dict) -> dict:
    """Convert a legacy Nearby Search result into a GeoJSON point feature."""
    loc = place["geometry"]["location"]
    props = {
        "name": place.get("name", "Basketball Court"),
        "address": place.get("vicinity", ""),
        "source": "Google",
    }
    if place.get("rating") is not None:
        props["rating"] = place["rating"]
    if place.get("user_ratings_total") is not None:
        props["reviews"] = place["user_ratings_total"]
    if place.get("place_id"):
        props["google_maps_uri"] = (
            f"https://www.google.com/maps/place/?q=place_id:{place['place_id']}"
        )
    if place.get("business_status"):
        props["status"] = place["business_status"]
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {
            "type": "Point",
            "coordinates": [round(loc["lng"], 6), round(loc["lat"], 6)],
        },
    }


def fetch_from_api() -> dict[str, dict] | None:
    """Run the adaptive Nearby Search, returning {place_id: raw_place}."""
    api_key = load_api_key()
    if not api_key:
        print("GOOGLE_MAPS_API_KEY not found. Set it in your environment or .env.", file=sys.stderr)
        return None
    try:
        return search_adaptive(api_key)
    except Exception as error:  # noqa: BLE001 - surface any network/API error
        print(f"Nearby Search failed: {error}", file=sys.stderr)
        return None


def load_cache() -> dict[str, dict] | None:
    """Load the raw results saved by a previous live run."""
    if not RAW_CACHE_PATH.exists():
        print(f"No cache at {RAW_CACHE_PATH}. Run `python courts.py` once first.", file=sys.stderr)
        return None
    return json.loads(RAW_CACHE_PATH.read_text(encoding="utf-8"))


def write_geojson(path: Path, places: list[dict]) -> None:
    features = [to_feature(place) for place in places]
    features.sort(key=lambda f: f["properties"]["name"].lower())
    collection = {"type": "FeatureCollection", "features": features}
    with path.open("w", encoding="utf-8") as file:
        json.dump(collection, file, ensure_ascii=False)


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from_cache = "--from-cache" in sys.argv

    unique = load_cache() if from_cache else fetch_from_api()
    if unique is None:
        return 1
    if not unique:
        print("No courts found - aborting without overwriting data.", file=sys.stderr)
        return 1

    # Persist the raw results from a live run so the filter can be re-run offline.
    if not from_cache:
        RAW_CACHE_PATH.write_text(json.dumps(unique, ensure_ascii=False), encoding="utf-8")
        print(f"Cached {len(unique)} raw results to {RAW_CACHE_PATH}")

    places = list(unique.values())
    if "--no-filter" not in sys.argv:
        kept = [p for p in places if keep_place(p)]
        dropped = [p for p in places if not keep_place(p)]
        print(f"Filter: kept {len(kept)}, dropped {len(dropped)} likely-condo/noise:")
        for place in sorted(dropped, key=lambda p: p.get("name", "").lower()):
            print(f"    - {place.get('name', '?')}")
        write_geojson(DROPPED_PATH, dropped)
        print(f"Wrote dropped entries (for review) to {DROPPED_PATH}")
        places = kept

    write_geojson(OUTPUT_PATH, places)
    print(f"Saved {len(places)} courts to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
