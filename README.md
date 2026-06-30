# MoveSG

A fitness map for Singapore. Find the basketball courts, parks, park connectors
and swimming pools around you — see them on a map, browse the closest ones in a
ranked list, and jump straight to Google Maps for ratings and directions.

MoveSG is a static website: HTML, CSS and vanilla JavaScript built on
[Leaflet](https://leafletjs.com/). The places it shows come from GeoJSON files
that live in the repo, generated ahead of time by the Python scripts in
`scripts/`. Nothing is fetched from a paid API while you browse.

## What you can do

- Toggle four layers independently — **parks**, **park connectors**, **basketball
  courts** and **swimming pools** — each drawn in its own colour and shape.
- Set where "near you" means: type a **6-digit postal code** or hit **use my
  location**. A pin drops at that point and the sidebar re-ranks every place by
  distance from it.
- Open any place for its **Google rating, review count and address**, plus a link
  out to Google Maps.
- Switch between **light and dark** themes — it follows your system setting and
  remembers your choice.

Until you search or share your location, the map sits over central Singapore.

## Quick start

It's a static site, but the app loads its data over HTTP, so serve the folder
rather than opening the file directly:

```bash
python -m http.server 8000
```

Open <http://localhost:8000>. No build, no dependencies, no setup for the site
itself.

## What's in the repo

| Path | What it is |
| --- | --- |
| `index.html` | Page markup — sidebar, map, theme toggle |
| `style.css` | Layout and light/dark theming via CSS variables |
| `app.js` | Everything the app does: map, layers, search, ranking, theme |
| `data/*.geojson` | The places the site loads (generated — see below) |
| `scripts/*.py` | The data-collection pipeline |

## Where the data comes from

Each layer is one GeoJSON file under `data/`. Official government data is
preferred; Google fills the gaps. The browser reads only these static files — it
never talks to Google.

| Layer | Source | Needs a Google key |
| --- | --- | --- |
| Parks | NParks "Parks & Nature Reserves" (data.gov.sg) | No |
| Park connectors | NParks "Park Connector Loop" (data.gov.sg) | No |
| Basketball courts | Google Places (legacy Nearby Search) | Yes |
| Swimming pools | Official ActiveSG list, geocoded via Google Places | Yes |

## Regenerating the data

The GeoJSON is committed, so you only need this to refresh it. The scripts use
Python 3.10+ and the standard library only.

The two government layers need no key:

```bash
python scripts/parks.py
python scripts/pcn.py
```

The two Google layers need a Maps API key. Copy the template and fill it in —
`.env` is git-ignored, so the key never gets committed or shipped to the browser:

```bash
cp .env.example .env      # then set GOOGLE_MAPS_API_KEY
python scripts/courts.py
python scripts/pools.py
```

A few notes on the Google scripts:

- **`courts.py`** uses the legacy Nearby Search, whose keyword matching reaches
  into reviews — that's how it finds unnamed void-deck and HDB courts. It sweeps
  Singapore with an adaptive grid of search circles, splitting only the dense
  ones that hit Google's 60-result cap, so cost tracks court density. It caches
  every raw result, so you can re-filter for free:

  ```bash
  python scripts/courts.py               # call Google, cache, filter, save
  python scripts/courts.py --from-cache  # re-filter the cache, no API calls
  python scripts/courts.py --no-filter   # keep everything, skip the noise filter
  ```

  Because reviews match too, condos and phantom blocks creep in; a name keep-list
  removes them, and whatever it drops is written to
  `data/courts_dropped.geojson` so you can check it.

- **`pools.py`** geocodes a hard-coded list of official ActiveSG complexes with
  the Places API (New). Edit the `OFFICIAL_POOLS` list to add or remove pools.

Enable both APIs in the
[Google Cloud Console](https://console.cloud.google.com/). Google requests are
billed per request — watch your usage.

## Deploying

Any static host works. For GitHub Pages: **Settings → Pages → Deploy from a
branch**, pick `main` and `/ (root)`, and the site goes live at
`https://<user>.github.io/<repo>/`.

## Credits

- Map tiles by [CARTO](https://carto.com/attributions); map data ©
  [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.
- Postal-code geocoding by [OneMap](https://www.onemap.gov.sg/).
- Parks and park connectors © National Parks Board via
  [data.gov.sg](https://data.gov.sg/), under the
  [Singapore Open Data Licence](https://data.gov.sg/open-data-licence).
- Court and pool details from the Google Places API — check Google's
  [terms](https://cloud.google.com/maps-platform/terms) before redistributing.
