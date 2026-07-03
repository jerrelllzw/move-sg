/* MoveSG service worker — offline-capable PWA.
 *
 * Strategy:
 *  - App shell (HTML/CSS/JS, icons, local GeoJSON) is precached on install.
 *  - Navigation requests: network-first, falling back to the cached shell so
 *    the app opens offline.
 *  - Same-origin data + CDN assets (Leaflet, Google Fonts): stale-while-revalidate.
 *  - Map tiles (CARTO basemap): cache-first with a capped runtime cache so
 *    previously-viewed areas stay visible offline without unbounded growth.
 *  - Anything else falls through to the network.
 *
 * Bump CACHE_VERSION whenever the shell asset list changes to roll caches.
 */
const CACHE_VERSION = "v1";
const SHELL_CACHE = `movesg-shell-${CACHE_VERSION}`;
const RUNTIME_CACHE = `movesg-runtime-${CACHE_VERSION}`;
const TILE_CACHE = `movesg-tiles-${CACHE_VERSION}`;
const TILE_CACHE_LIMIT = 400;

// Core same-origin assets — install fails if any of these can't be cached.
const SHELL_ASSETS = [
  ".",
  "index.html",
  "style.css",
  "app.js",
  "favicon.svg",
  "icon-192.png",
  "icon-512.png",
  "icon-maskable-512.png",
  "manifest.webmanifest",
  "data/parks.geojson",
  "data/pcn.geojson",
  "data/courts.geojson",
  "data/pools.geojson",
];

// Cross-origin assets needed to render the shell — best-effort (opaque, may fail).
const CDN_ASSETS = [
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
  "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      // Core assets must succeed; add them atomically.
      await cache.addAll(SHELL_ASSETS);
      // CDN assets are optional at install time — don't block install if offline
      // or a CDN hiccups; they'll be picked up at runtime instead.
      await Promise.allSettled(
        CDN_ASSETS.map((url) =>
          fetch(url, { mode: "no-cors" })
            .then((res) => cache.put(url, res))
            .catch(() => {})
        )
      );
      await self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set([SHELL_CACHE, RUNTIME_CACHE, TILE_CACHE]);
      const names = await caches.keys();
      await Promise.all(
        names.map((name) => (keep.has(name) ? null : caches.delete(name)))
      );
      await self.clients.claim();
    })()
  );
});

function isTileRequest(url) {
  return url.hostname.endsWith("basemaps.cartocdn.com");
}

function isCdnAsset(url) {
  return (
    url.hostname === "unpkg.com" ||
    url.hostname === "fonts.googleapis.com" ||
    url.hostname === "fonts.gstatic.com"
  );
}

// Serve from cache, update in the background.
async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((res) => {
      if (res && (res.ok || res.type === "opaque")) {
        cache.put(request, res.clone());
      }
      return res;
    })
    .catch(() => null);
  return cached || (await network) || Response.error();
}

// Cache-first for tiles, with a rough LRU-ish size cap.
async function cacheFirstTile(request) {
  const cache = await caches.open(TILE_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  try {
    const res = await fetch(request);
    if (res && (res.ok || res.type === "opaque")) {
      cache.put(request, res.clone());
      trimCache(TILE_CACHE, TILE_CACHE_LIMIT);
    }
    return res;
  } catch (err) {
    return cached || Response.error();
  }
}

async function trimCache(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= maxEntries) return;
  // Delete oldest entries (Cache keys() preserves insertion order).
  const excess = keys.length - maxEntries;
  for (let i = 0; i < excess; i++) {
    await cache.delete(keys[i]);
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // App navigations: network-first so users get fresh HTML online, cached shell offline.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(async () => {
        const cache = await caches.open(SHELL_CACHE);
        return (
          (await cache.match(request)) ||
          (await cache.match("index.html")) ||
          (await cache.match("."))
        );
      })
    );
    return;
  }

  if (isTileRequest(url)) {
    event.respondWith(cacheFirstTile(request));
    return;
  }

  if (isCdnAsset(url)) {
    event.respondWith(staleWhileRevalidate(request, RUNTIME_CACHE));
    return;
  }

  // Same-origin assets and data (GeoJSON): stale-while-revalidate.
  if (url.origin === self.location.origin) {
    event.respondWith(staleWhileRevalidate(request, SHELL_CACHE));
    return;
  }

  // Everything else (e.g. OneMap search, Google Maps links): straight to network.
});
