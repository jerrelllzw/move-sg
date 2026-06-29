# MoveSG 🏀🏞️🏊

Your personalized fitness map for Singapore — basketball courts, parks, park
connectors and swimming pools, all in one place.

It's a full-screen map with four equal layers — basketball courts, parks, park
connectors and swimming pools. Toggle any layer on or off, tap **Near me** to
jump to your location, and click any marker or shape for details and one-tap
directions.

![MoveSG](https://github.com/user-attachments/assets/2e5ec3a2-88f5-4a6b-83c0-05d2ab14d275)

## Features

- 🗂️ **Four equal layers** — 🏀 courts, 🏞️ parks, 🌳 park connectors, 🏊 pools,
  each independently toggleable (all on by default).
- 📍 **Near me** — geolocation drops a marker and centres the map on you.
- 🗺️ **Interactive map** — built on Leaflet + OpenStreetMap (no API key needed).
- 🧭 **Directions** — click any place for one-tap Google Maps directions.
- 📱 **Mobile-friendly** — responsive layout for phones and desktop.

## How it works

The app is a static site (`index.html`, `style.css`, `app.js`) — no backend and
**no API keys at runtime**. Each layer just loads a static data file from `data/`.

The data is collected offline by the Python scripts. Collection and the website
are fully decoupled: refresh whenever you like, commit the updated files, and the
site serves them. **API keys live only in the data-collection step**, never in
the browser.

Sources follow a "official government data first, then Google" preference:

| Layer | File | Source | Key needed? |
| --- | --- | --- | --- |
| 🌳 Park connectors | [`data/pcn.geojson`](data/pcn.geojson) | data.gov.sg — NParks Park Connector Loop | No |
| 🏞️ Parks | [`data/parks.geojson`](data/parks.geojson) | data.gov.sg — NParks Parks & Nature Reserves | No |
| 🏀 Basketball courts | [`data/courts.geojson`](data/courts.geojson) | Google Places API (New) | Yes |
| 🏊 Swimming pools | [`data/pools.geojson`](data/pools.geojson) | Official ActiveSG list, geocoded via Google Places | Yes |

The swimming-pool layer is the canonical ActiveSG list of public complexes
(hard-coded in [`scripts/pools.py`](scripts/pools.py)); each name is geocoded
with Google.

## Project structure

```
.
├── index.html, style.css, app.js   # the static site (served as-is)
├── data/                           # generated GeoJSON the site reads
│   ├── courts.geojson  parks.geojson  pcn.geojson  pools.geojson
├── scripts/                        # offline data-collection pipeline
│   ├── courts.py  pools.py         # Google Places layers
│   ├── parks.py   pcn.py           # data.gov.sg layers
│   ├── datagovsg.py                # data.gov.sg download helper
│   └── google_places.py            # Google Places + key loader
├── .env / .env.example             # Google key (git-ignored) + template
└── README.md
```

## Running locally

It's a static site, so any static server works. With Python:

```bash
python -m http.server 8000
# then open http://localhost:8000
```

It's a static site, so any static server works. With Python:

```bash
python -m http.server 8000
# then open http://localhost:8000
```

## Refreshing the data

### Government layers (no key)

```bash
python scripts/pcn.py     # -> data/pcn.geojson     (Park Connector Network)
python scripts/parks.py   # -> data/parks.geojson   (parks)
```

### Google layers (needs a key)

1. Copy `.env.example` to `.env` and add your key (with **Places API (New)**
   enabled). `.env` is git-ignored, so the key is never committed or deployed.
2. Run:

   ```bash
   python scripts/courts.py   # -> data/courts.geojson  (basketball courts)
   python scripts/pools.py    # -> data/pools.geojson   (ActiveSG list via Google)
   ```

`scripts/pools.py` geocodes the official ActiveSG list of complexes — edit the
`OFFICIAL_POOLS` list in the script to add or remove pools.

`scripts/courts.py` runs a text search across a grid of map cells (tune
`GRID_ROWS` / `GRID_COLS` in the script). **Google Text Search is billed per
request**, so a finer grid means better coverage but a larger bill — keep an eye
on your usage.

## Deploying (GitHub Pages)

1. Push to GitHub.
2. **Settings → Pages → Build and deployment → Source: Deploy from a branch**.
3. Choose the `main` branch and the `/ (root)` folder, then **Save**.

The site will be live at `https://<your-username>.github.io/<repo>/`.

## Data & attribution

- Base map © [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors (ODbL).
- Parks and park connectors © National Parks Board, via
  [data.gov.sg](https://data.gov.sg/) under the
  [Singapore Open Data Licence](https://data.gov.sg/open-data-licence).
- Basketball courts and swimming pools from the Google Places API — review
  Google's [Places API terms](https://cloud.google.com/maps-platform/terms)
  before redistributing this data.
