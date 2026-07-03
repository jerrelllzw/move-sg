# MoveSG

A fitness map for Singapore. Find the basketball courts, parks, park connectors
and swimming pools around you — see them on a map, browse the closest ones in a
ranked list, and jump straight to Google Maps for ratings and directions.

MoveSG is a static website: HTML, CSS and vanilla JavaScript built on
[Leaflet](https://leafletjs.com/). The places it shows come from GeoJSON files
that live in the repo, generated ahead of time by the Python scripts in
`scripts/`. Nothing is fetched from a paid API while you browse.

It's also an installable, offline-capable **Progressive Web App** — add it to
your home screen and it opens full-screen and works without a connection.

## What you can do

- Toggle four layers independently — **parks**, **park connectors**, **basketball
  courts** and **swimming pools** — each drawn in its own colour and shape.
- Set where "near you" means: search by **postal code, street or place name**
  (with live suggestions as you type) or hit **use my location**. A pin drops at
  that point and the sidebar re-ranks every place by distance from it.
- Open any place for its **Google rating, review count and address**, plus a link
  out to Google Maps.
- Switch between **light and dark** themes — it follows your system setting and
  remembers your choice.
- **Install it** to your phone or desktop and use it **offline** — the app,
  its place data and any map areas you've already viewed are cached.

Until you search or share your location, the map sits over central Singapore.

## Quick start

It's a static site, but the app loads its data over HTTP, so serve the folder
rather than opening the file directly:

```bash
python -m http.server 8000
```

Open <http://localhost:8000>. No build, no dependencies, no setup for the site
itself.

## Install & offline

MoveSG is a Progressive Web App. `manifest.webmanifest` makes it installable
(**Add to Home Screen** on mobile, or the install icon in the address bar on
desktop), and `sw.js` is a service worker that caches for offline use:

- The **app shell** (HTML/CSS/JS, icons) and all **place data** are precached, so
  the app opens and the lists work with no connection.
- **Map tiles** are cached as you view them — areas you've already panned over
  stay visible offline; brand-new areas show blank tiles until you're back online.
- Online, it stays fresh: navigations are network-first and assets update in the
  background (stale-while-revalidate).

Service workers only run over **HTTPS** (or `localhost`), so offline support is
active on any real deploy and when testing locally — but not when opening the
file directly. Bump `CACHE_VERSION` in `sw.js` whenever you change the cached
asset list to roll the caches.

## What's in the repo

| Path | What it is |
| --- | --- |
| `index.html` | Page markup — sidebar, map, theme toggle |
| `style.css` | Layout and light/dark theming via CSS variables |
| `app.js` | Everything the app does: map, layers, search, ranking, theme |
| `sw.js` | Service worker — offline caching of the app shell, data and map tiles |
| `manifest.webmanifest` | PWA manifest — name, icons, colours, standalone display |
| `favicon.svg`, `icon-*.png` | App icons (SVG plus 192/512 PNGs, including a maskable one) |
| `data/*.geojson` | The places the site loads (generated — see below) |
| `data/*.json` | Raw API caches, so the data can be re-filtered/re-run for free |
| `scripts/*.py` | The data-collection pipeline (plus shared `datagovsg`, `google_places` and `utf8_console` helpers) |

## Where the data comes from

Each layer is one GeoJSON file under `data/`. Official government data is
preferred; Google fills the gaps and supplies place links, ratings and reviews.
The browser reads only these static files — it never talks to Google.

| Layer | Source | Google key |
| --- | --- | --- |
| Parks | NParks "Parks & Nature Reserves" (data.gov.sg) | Optional — enriches each park with a canonical Google Maps link, rating and address |
| Park connectors | NParks "Park Connector Loop" (data.gov.sg) | Optional — same enrichment, per unique connector name |
| Basketball courts | Google Places (legacy Nearby Search) | Required |
| Swimming pools | Official ActiveSG list, geocoded via Google Places | Required |

The government layers still generate fine without a key — they just ship without
the Google link/rating, and the app falls back to a name search for those.

## Regenerating the data

The GeoJSON is committed, so you only need this to refresh it. The scripts use
Python 3.10+ and the standard library only.

All four scripts read the Google key from a `.env` file. Copy the template and
fill it in — `.env` is git-ignored, so the key never gets committed or shipped to
the browser:

```bash
cp .env.example .env      # then set GOOGLE_MAPS_API_KEY
```

The two government layers download from data.gov.sg (no key needed for that) and
then geocode each place against Google to attach a link, rating and address. With
no key they still run — just without that enrichment. Each geocode is cached to
`data/parks_places.json` / `data/pcn_places.json`, so a re-run only looks up new
places and is resumable if interrupted:

```bash
python scripts/parks.py
python scripts/pcn.py
```

The two Google layers require the key:

```bash
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
- Location search and geocoding by [OneMap](https://www.onemap.gov.sg/).
- Parks and park connectors © National Parks Board via
  [data.gov.sg](https://data.gov.sg/), under the
  [Singapore Open Data Licence](https://data.gov.sg/open-data-licence).
- Court and pool details from the Google Places API — check Google's
  [terms](https://cloud.google.com/maps-platform/terms) before redistributing.
