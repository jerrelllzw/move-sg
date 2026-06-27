# SG Courts 🏀

A web app to find basketball courts near you in Singapore.

Pick **Find courts near me**, allow location access, and the app sorts every
court by how far it is from you, shows them on a map, and links you straight to
walking/driving directions.

![SG Courts](https://github.com/user-attachments/assets/2e5ec3a2-88f5-4a6b-83c0-05d2ab14d275)

## Features

- 📍 **Near me** — geolocation sorts courts by real distance (haversine).
- 🗺️ **Interactive map** — built on Leaflet + OpenStreetMap (no API key needed).
- 🔎 **Search** — filter courts by name.
- 🧭 **Directions** — one tap opens Google Maps directions to the court.
- 📱 **Mobile-friendly** — responsive layout for phones and desktop.

## How it works

The app is a static site (`index.html`, `style.css`, `app.js`) — no backend.
Court data lives in [`data/courts.json`](data/courts.json) and is loaded in the
browser.

Data comes from [OpenStreetMap](https://www.openstreetmap.org/) via the
[Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API), so it's free,
open, and refreshable.

## Running locally

It's a static site, so any static server works. With Python:

```bash
python -m http.server 8000
# then open http://localhost:8000
```

## Refreshing the court data

Re-run the scraper to pull the latest courts from OpenStreetMap. It only needs
the Python standard library (no API key, no dependencies):

```bash
python courts.py
```

This rewrites [`data/courts.json`](data/courts.json).

## Deploying (GitHub Pages)

1. Push to GitHub.
2. **Settings → Pages → Build and deployment → Source: Deploy from a branch**.
3. Choose the `main` branch and the `/ (root)` folder, then **Save**.

The site will be live at `https://<your-username>.github.io/<repo>/`.

## Data & attribution

Court data © [OpenStreetMap](https://www.openstreetmap.org/copyright)
contributors, available under the ODbL.
