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
# This is a basketball map, so drop futsal/badminton/tennis/volleyball and
# football/street-soccer courts — unless the name also says "basketball" (a
# multi-sport hardcourt that includes one).
OTHER_SPORT_RE = re.compile(
    r"\b(?:futsal|badminton|tennis|volleyball|football|soccer)\b", re.I
)


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
    # Manually reviewed venues whose NAME passes KEEP_PATTERNS (community club,
    # park, sport hall, stadium, academy...) but which aren't a public basketball
    # court — companies, residents' committees, gyms, stadiums, etc. Excluded so a
    # re-run doesn't repopulate them into courts.geojson.
    "ChIJT4pZ2m4T2jERBmcE2uLFBk0",  # 200 Woodlands Industrial Park E7
    "ChIJC74KtocX2jERbjuFZt5Btkw",  # ActiveSG Gym @ Fernvale Square
    "ChIJDZ3Ue80b2jEROkyEYadw5tU",  # Alexandra Hill East Neighbourhood Park
    "ChIJydFa4-AZ2jERnphWgaLeBJc",  # ANTA BASKETBALL (Paragon)
    "ChIJEwIC5BI92jERO8rhRUL8UK0",  # Arena @ OTH
    "ChIJgx4YWagR2jERCasQYF6YSKI",  # ARK Sports Village
    "ChIJfY8JOIgF2jERYc3bivJ2uQc",  # B. T. Sports Pte Ltd
    "ChIJG9eysQ4Z2jER2ssnNHMkxvI",  # Basketball Association of Singapore
    "ChIJ7Uv74dw92jERN4kNOjK5ErA",  # Bedok ActiveSG Sports Hall
    "ChIJ1aYdSzU92jERCCLawGTW3II",  # Bedok Stadium
    "ChIJq39FCPQX2jER34zgRQRenq4",  # Bidadari Community Club
    "ChIJq8Wn7BQX2jERkoyjQG8mke8",  # Bishan East Zone 1 Residents' Committee
    "ChIJr7mzHhoX2jERI6aOwsevkcA",  # Bishan Sport Hall
    "ChIJx7bZHBoX2jER-hecDyx4avo",  # Bishan Sports and Recreation Centre
    "ChIJU_zBbRQX2jERb37wh0APVP8",  # Bishan Street 13 Pavilion
    "ChIJXxLuJ5gX2jERg_-g4i1jDWI",  # Blue Court
    "ChIJLb1hKZUP2jERy8XqSPsCLgo",  # Boon Lay Zone B Residents' Network
    "ChIJgW53_2cW2jERRQ8UJhZT5jI",  # Buangkok Sports Park
    "ChIJi3pXvD8Q2jERSz5q5ag3ndw",  # Bukit Batok Zone 7 Residents' Network
    "ChIJYe_UNiYV2jERlvWhqzwZneA",  # Bukit Canberra Sport Centre
    "ChIJByE8bGAV2jEReIAf4jZEwD0",  # Bukit Canberra Sport Hall
    "ChIJLzOhyEkQ2jERD-0pECpabro",  # Bukit Gombak ActiveSG Gym
    "ChIJcRKWnisR2jERJQWjOOkVyIY",  # Bukit Gombak Park Dog Run
    "ChIJKydd_kkQ2jERP8621RAPPz4",  # Bukit Gombak Sport Hall
    "ChIJJWt786YR2jERcIgIkdUxv_0",  # Bukit Panjang Community Club
    "ChIJGacQ0GMQ2jER8wwsO3AmbKg",  # Bukit Timah Community Club
    "ChIJRzglD-wR2jERgCmyEeAecXI",  # Choa Chu Kang Park
    "ChIJ_WX9ue0R2jER865IJyACAoc",  # Choa Chu Kang Sport Centre
    "ChIJSfXDmI0a2jERDLaKw4Sp5Tc",  # Clementi ActiveSG Gym
    "ChIJi6fqmI0a2jERdXJz8ecovMg",  # Clementi Sport Hall
    "ChIJtwnG1kka2jERN1yAwHYFtkQ",  # Commonwealth Park
    "ChIJy5EXsyoa2jERNfQ1b93bPI4",  # Delta ActiveSG Gym
    "ChIJyWQnEYkb2jER6BxhOn5f-BA",  # Delta Sport Centre
    "ChIJG9pxkCoa2jERSyubgxNCy-k",  # Delta Sport Hall
    "ChIJx63zejEW2jERv3c5XCqjr-8",  # Evergreen Park
    "ChIJRYg7aTgW2jERzmol5yapSgg",  # Exercise Park
    "ChIJkVO-z5gR2jERYR1mMHhBYc8",  # Fajar Sports Hub
    "ChIJbbOUMgAR2jER7KJytxaGdzY",  # Fitness Corner I @Limbang Pk
    "ChIJ23HbOQAT2jERYweY6awYYms",  # Fitness Corner, Fu Shan Gdn
    "ChIJe51FQbwP2jERj_QFCJP_xnM",  # Gek Poh Ville Community Club
    "ChIJC2ORsH0R2jERx6MKPcbMjCA",  # Goodview Gardens Park
    "ChIJ1xgTQ7Ui2jERqLDkdtiHQl8",  # Heartbeat @ Bedok ActiveSG Swimming Complex
    "ChIJo5Mh_EgW2jERwPa6qOD7A7Y",  # Hougang Sport Hall
    "ChIJ0Rz6Sg0Y2jERmgd6ZjD41YE",  # Joo Chiat Community Club
    "ChIJEwW7gpoP2jER4o0mrMTZddM",  # Jurong West Sport Centre
    "ChIJYeredpAP2jERduiD1Z5dTGI",  # Jurong West Sport Hall
    "ChIJD_-Au5QX2jERPo8cB1pklg8",  # Kohup Sports Pte Ltd
    "ChIJ9S391fwX2jERDralw-JiEVs",  # KOTC Sports Pte Ltd
    "ChIJxa0bsEIS2jER3pv2smVyDcs",  # Kranji Recreation Centre
    "ChIJywqE63IZ2jER4NBNaHlGSv0",  # Kreta Ayer Community Club
    "ChIJlwhGYVU92jERIKor-3Jyvvs",  # Lengkong Enam Park
    "ChIJHxDcuhgX2jERvyzRhopMHII",  # Marymount Community Club
    "ChIJT-tysI4P2jERYQT45sS525E",  # Migrant Workers' Centre Recreation Club (MWC RC)
    "ChIJb7dKT2IP2jERcFRITIwWDEY",  # MWC Recreation Club (MWC FWSIP)
    "ChIJq7C_6m8U2jERTnac8Wp_HgY",  # Nee Soon Central Community Club
    "ChIJq6qqmhEU2jER0yE06FDEo-U",  # Nee Soon South Community Club
    "ChIJd2DnQXEU2jER4p6relpiLuk",  # Nee Soon South Zone D Residents' Network
    "ChIJfePSLm8U2jERApXFZbuSbLo",  # Nee Soon Sports Centre
    "ChIJVVJ93ocQ2jER8osZOv0lKQA",  # Ngee Ann Sports Complex
    "ChIJKfr7nG8U2jERGyp7fB-uPTA",  # North Park Residences
    "ChIJKR6br5QW2jERSsm0JDaPgkY",  # NYP Stadium
    "ChIJjSbbY6Y92jER16q5yg_NPkI",  # OBA Arena @ Pasir Ris
    "ChIJxUtilYEV2jERIbEmG9kxetY",  # OBA Arena @ Punggol
    "ChIJrUlEWhI92jERvt94DySkZLg",  # Our Tampines Hub ActiveSG Community Auditorium
    "ChIJURXsNa092jERHMncG26FzIo",  # Pasir Ris East Community Club
    "ChIJuY8mPsc92jERdf13nPQ8Pq4",  # Pasir Ris Park Adventure Playground
    "ChIJ9xq4i60a2jERtrsKmFap4qA",  # Penjuru Recreation Centre
    "ChIJkwp7xqUR2jERnoHV9NhCArc",  # Petir Park
    "ChIJq1FFDiEZ2jERadqa_RcvBh4",  # Real Madrid Foundation Basketball School Paya Lebar
    "ChIJGXpRrr3Pq0YR_05Y3-CH1_c",  # Scholar Basketball Academy
    "ChIJLZrxN-0R2jERvAlzlCDCtwk",  # Scholar Basketball Academy (Bukit Timah Plaza)
    "ChIJO_I9wSIR2jERrn9XgYZYEkw",  # Segar Gardens Court
    "ChIJ7ZgOOiAV2jERCFeUkkIYSag",  # Sembawang Recreation Centre
    "ChIJZ96CCXQW2jERfzExgYN3pfw",  # Sengkang Sports Hall
    "ChIJX5aljeQX2jERXdgdcLOa1kk",  # SG Basketball @ KBRC
    "ChIJm3O-ybYj2jERoNzZBoBl-Po",  # SG Basketball @ Shot Zone
    "ChIJfwlSIt482jER7yY_s9mjfHg",  # SIA Sports Club
    "ChIJ42aqIOkj2jERYHGec0xobYw",  # Siglap Community Club
    "ChIJu9z--xkX2jERWTaBRwAOhG4",  # SMRT Recreation Club
    "ChIJ1fYfwGAa2jERGh-sWjfddQI",  # SP Sports Arena
    "ChIJXU7drEcQ2jERbWPDktMIwlQ",  # Sports & Recreation Centre
    "ChIJg3-7Glwb2jERA--oUIY-tD4",  # Sports @ Buona Vista
    "ChIJs6XD7d482jERSe1xuAHuVjY",  # SUTD Sports and Recreation Centre
    "ChIJz-uAfxI92jERZW0Qc-ylPPQ",  # Tampines Sports Hall
    "ChIJ4yN1GYcP2jERQxzuheuvbVI",  # TCB Sports Pte Ltd
    "ChIJGXaMysIb2jERUq9kRyJeaJ4",  # Telok Blangah 'Blangah Court' Residents' Committee
    "ChIJCflFZ8Mb2jERXM0PHAKxCKE",  # Telok Blangah 'Blangah Square' RC
    "ChIJh53Jei8X2jERfZdFMkoBje0",  # Thomson Sin Ming Court Residents' Committee
    "ChIJERyP1dZwvaYR20PXbILdkNY",  # Triple Threat | Basketball Academy Singapore
    "ChIJdc6XlNkZ2jERAdhP0MWbePc",  # Whampoa View Residents' Committee
    "ChIJV5hnA6wT2jERqD4CwNu0SpM",  # Woodlands Sport Centre
    "ChIJ9-wMbKkT2jERJojJzLw7pdA",  # Woodlands Sport Hall
    "ChIJ2_q9-asT2jERy4IMLTN6_d8",  # Woodlands Stadium
    "ChIJ4-iWlw8U2jERbtvdchPy4-o",  # Yishun Sport Hall
    # Near-duplicate cluster review (2026-07): pairs/venues within ~70m of a more
    # specific basketball-court entry; kept the court, dropped the venue/lower-review
    # sibling. See data/courts_dropped.geojson for the removed pins.
    "ChIJjVfWj3gX2jERHP9kSEkgmOM",  # Potong Pasir Community Club (-> Potong Pasir Basketball Court)
    "ChIJE6iNrJ4a2jERMMx8SP8R3bM",  # Faber Height Open Space (-> Basketball Court)
    "ChIJv-oYL1IX2jERS4HEVHik3S0",  # The Walk at Buangkok Recreation Park (-> Sheltered BC @ Buangkok Green Blk 987)
    "ChIJ7bQAOJYX2jERxGt976pkSNI",  # Thrift Drive Open Space (-> TDOS Basketball Court)
    "ChIJx-6Lg4IZ2jERfBWa3SzWvA8",  # Kim Seng Community Centre (-> Kim Seng CC Basketball court)
    "ChIJJxiLHg892jER1e5MNA9iGKI",  # Tampines North Community Club (-> Basketball Court @ Tampines North CC)
    "ChIJJYncfsAR2jERVE0WPGMg8Bg",  # Chua Chu Kang Community Club (-> Choa Chu Kang CC Sheltered BC)
    "ChIJW2RO4zE92jERB0zfdzfmxzc",  # Tampines West Community Club (-> Sheltered BC @ Tampines West CC)
    "ChIJ6zYOHVoW2jERqI7iqOoGeTo",  # Hwi Yoh Community Centre (-> Blk 537 Basketball Court)
    "ChIJ1wjHMWEW2jERdg_PSN-cpr4",  # Jalan Selaseh Park (-> JSP Basketball Court)
    "ChIJlaOQkfgP2jERAbtDE12iPEw",  # Taman Jurong Park (-> Basketball Court (Sheltered))
    "ChIJm1F3HUo92jERUztUGDVbOYI",  # Fengshan Community Club (-> Fengshan CC Basketball Court)
    "ChIJ65I0z9gb2jERMbHTZo81AoQ",  # Radin Mas Community Club (-> Radin Mas CC Sheltered BC)
    "ChIJ_4KZj9cZ2jERsIv_LOKK2W0",  # Whampoa Community Club (-> Basketball Court @ Whampoa CC)
    "ChIJz8Tq5nMT2jERBtZQj6riGo8",  # Woodlands Crescent Park (-> WCP Basketball Court)
    "ChIJPRrhkhoY2jERMMNSzjg2Tkg",  # Kampong Ubi Community Centre (-> Kampong Ubi CC basketball court)
    "ChIJDXSq2WAT2jERo3eAWsc06vA",  # Jelutung Harbour Park (-> Basketball Court, Woodlands Sector 2)
    "ChIJOQsPkFMW2jERCnF63_cwx38",  # Activity Park (-> SNCP Basketball Court)
    "ChIJq_t9CxIU2jERsR9RNw6kk7E",  # MPC @ Khatib (-> Basketball Court)
    "ChIJLRrXHfgX2jERL0NiN2Ht2Fw",  # 332 Basketball Court (-> Blk 333 Basketball Court, higher reviews)
    "ChIJvfZ-fwAX2jERpCznT7QkGeM",  # 188 Basketball Court (-> Elias Basketball Court, higher reviews)
    "ChIJY1ND0NMR2jEROE2Ap7Ehum4",  # Basketball court near blue pavillion (-> South View LRT Basketball Court)
    "ChIJj7HC34EX2jERr-X7PbtuROY",  # Potong Pasir Town Basketball Court (-> Blk 222 Basketball Court, higher reviews)
    "ChIJT06NDgAV2jERAhBWD10wdDc",  # Blk 216 basketball court (-> Block 213 Basketball Court)
    "ChIJh-wRcEkT2jER7tHuARn_I2s",  # Basketball Court Block 366 (-> Basketball Court @ Canberra Park)
    "ChIJJdCFw34R2jERfyY5itp3Ef0",  # The Arena @ Keat Hong (-> Lam Soon Basketball Court)
    "ChIJI7qbDaEP2jERTZxqNlegpqM",  # NTU SRC Hall D (-> Sports & Recreation Centre)
    "ChIJqcFGhWoZ2jERLRNy_V4gRFQ",  # Heritage Garden Basketball Court (-> Pinnacle @ Duxton Basketball Court)
    "ChIJ6wMr92cX2jERQ0j8BFwdM5Y",  # Toa Payoh West Community Club (-> Basketball Court, 144 Lor 2 Toa Payoh)
    # Second cluster pass (~150m): more venue pins duplicating a nearby court entry.
    "ChIJw92PBzQW2jER1ffIIa59foY",  # Hougang Neighbourhood Park (-> 324 Hougang Ave Basketball Court)
    "ChIJ72UutBsQ2jERo9kuR_0VQVg",  # Yuhua Community Club (-> Basketball Court (Caged))
    "ChIJsdDhIQgY2jERTCtDVYq3uzs",  # Telok Kurau Park (-> Basketball court)
    "ChIJWzXCpUUW2jERt2u2OJN48A8",  # Ci Yuan Community Club (-> 918 Basketball Court)
    "ChIJ2V0hRQA92jERgzdNUe6N8OU",  # Multi-purpose court - Blk 138 Bedok North St 2 (-> Blk 138 … Basketball Court)
    "ChIJd-Ab69UR2jERF8gLf_TT-CQ",  # Old Court Basketball Court (-> Basketball court, same spot)
    "ChIJRVcadg0W2jERQLUnN_6QBCw",  # Sengkang Community Club (-> Sengkang Square Basketball Court)
    "ChIJcdzI0G8Z2jERIKVSm89vuKA",  # Blk 49 Kim Pong Rd - Multi-Purpose Court
    "ChIJNd06foAQ2jEREkyR1w_gZsQ",  # SIM Multi-Purpose Sports Hall (indoor sports hall, not a public court)
    "ChIJVzciVPMX2jEREHRLNPArDgc",  # Blk 923 Multi-Purpose Court (name/address mismatch)
    "ChIJk-mskEIU2jER9R7lkjG9qCk",  # Multipurpose Court (360 Yishun Ring Rd)
    "ChIJcZ2UoQoT2jERFzW6nLY05LI",  # Multi Purpose Court (unlocatable, addr "Singapore")
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
