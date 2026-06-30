"use strict";

const SG_CENTER = [1.3521, 103.8198];
const DEFAULT_ZOOM = 12;

// Each category is an independent, toggleable map layer; `kind` drives its
// geometry styling and stacking pane.
const CATEGORIES = [
  {
    id: "parks",
    label: "Parks",
    icon: "🌳",
    color: "#2ecc71",
    url: "data/parks.geojson",
    kind: "area",
  },
  {
    id: "pcn",
    label: "Park Connectors",
    icon: "🚴",
    color: "#8b5cf6",
    url: "data/pcn.geojson",
    kind: "line",
  },
  {
    id: "courts",
    label: "Basketball Courts",
    icon: "🏀",
    color: "#ff7a18",
    url: "data/courts.geojson",
    kind: "point",
  },
  {
    id: "pools",
    label: "Swimming Pools",
    icon: "🏊",
    color: "#2d7dff",
    url: "data/pools.geojson",
    kind: "point",
  },
];

const els = {
  status: document.getElementById("status"),
  locate: document.getElementById("locate-btn"),
  layerToggles: document.getElementById("layer-toggles"),
  toolbar: document.getElementById("toolbar"),
  toolbarToggle: document.getElementById("toolbar-toggle"),
  nearbyList: document.getElementById("nearby-list"),
  nearbyOrigin: document.getElementById("nearby-origin"),
  searchForm: document.getElementById("search-form"),
  addressInput: document.getElementById("address-input"),
  themeToggle: document.getElementById("theme-toggle"),
};

// How many of the closest places the sidebar lists.
const NEARBY_LIMIT = 40;

let originMarker = null;
let statusTimer = null;

// Every feature, flattened across categories, so the sidebar can rank them by
// distance from the reference point. `latlng` is the point itself, or the
// centroid for areas/lines; `layer` lets a list click open that feature's popup.
const places = [];
// The "near you" origin: central Singapore until geolocation gives us a real fix.
let referencePoint = L.latLng(SG_CENTER[0], SG_CENTER[1]);
// At most one feature is highlighted at a time. Tracking it globally is a safety
// net: if a feature's mouseout is ever missed, the next hover still clears it.
let hovered = null;

// --- Map setup ---------------------------------------------------------------

const map = L.map("map", { zoomControl: false }).setView(SG_CENTER, DEFAULT_ZOOM);

L.control.zoom({ position: "bottomright" }).addTo(map);

// CARTO basemap: a clean, low-contrast surface that lets the colored overlays
// (parks, connectors, courts, pools) read clearly. The {s}/{z}/{x}/{y}{r}
// placeholders are filled by Leaflet; {r} serves retina tiles. The light/dark
// variant is swapped by the theme toggle (see theme handling below).
const BASEMAP_URL = {
  light: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
  dark: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
};

const baseLayer = L.tileLayer(BASEMAP_URL.light, {
  maxZoom: 20,
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
}).addTo(map);

// Layers load concurrently, so use panes to fix stacking regardless of which
// finishes first: point markers sit above lines, which sit above area fills.
map.createPane("areaPane").style.zIndex = 410;
map.createPane("linePane").style.zIndex = 420;
map.createPane("pointPane").style.zIndex = 430;

// --- Helpers -----------------------------------------------------------------

function setStatus(message, autoHideMs) {
  clearTimeout(statusTimer);
  els.status.textContent = message || "";
  els.status.classList.toggle("show", Boolean(message));
  if (message && autoHideMs) {
    statusTimer = setTimeout(() => setStatus(""), autoHideMs);
  }
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// A compact star rating: filled/half/empty glyphs plus the numeric score and
// review count, matching how Google surfaces a place's reputation at a glance.
function ratingHtml(rating, reviews) {
  const full = Math.floor(rating);
  const half = rating - full >= 0.25 && rating - full < 0.75;
  const bonus = rating - full >= 0.75 ? 1 : 0;
  let stars = "★".repeat(full + bonus);
  if (half) stars += "⯨";
  stars += "☆".repeat(Math.max(0, 5 - full - bonus - (half ? 1 : 0)));

  const count =
    reviews != null
      ? ` <span class="popup-reviews">(${reviews.toLocaleString()})</span>`
      : "";
  return `<div class="popup-rating"><span class="popup-stars" aria-hidden="true">${stars}</span> <span class="popup-score">${rating.toFixed(1)}</span>${count}</div>`;
}

// Fall back to a Google Maps search by name+address when Google didn't hand us a
// canonical place URI for this feature.
function placeUrl(props) {
  if (props.google_maps_uri) return props.google_maps_uri;
  const query = [props.name, props.address].filter(Boolean).join(" ");
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
}

function popupHtml(category, feature) {
  const props = feature.properties || {};
  let html = `<div class="popup-title"><span class="popup-cat-icon" title="${escapeHtml(category.label)}" aria-label="${escapeHtml(category.label)}">${category.icon}</span> ${escapeHtml(props.name)}</div>`;

  if (typeof props.rating === "number") {
    html += ratingHtml(props.rating, props.reviews);
  } else {
    html += `<div class="popup-rating popup-empty">No ratings yet</div>`;
  }

  if (props.address) {
    html += `<div class="popup-detail">${escapeHtml(props.address)}</div>`;
  } else {
    html += `<div class="popup-detail popup-empty">No location available</div>`;
  }

  html += `<a class="popup-link" href="${placeUrl(props)}" target="_blank" rel="noopener">View on Google Maps →</a>`;

  return html;
}

// --- Layer building ----------------------------------------------------------

function buildLayer(category, geojson) {
  // Resting styles per geometry kind, and the extra emphasis applied on hover so
  // it's obvious which feature a click will hit.
  const baseStyles = {
    point: {
      radius: 7,
      color: "#fff",
      weight: 1.5,
      fillColor: category.color,
      fillOpacity: 0.95,
    },
    line: { color: category.color, weight: 3, opacity: 0.9 },
    area: {
      color: category.color,
      weight: 1,
      fillColor: category.color,
      fillOpacity: 0.25,
    },
  };
  const hoverStyles = {
    point: { radius: 10, weight: 3, color: category.color, fillColor: "#fff" },
    line: { weight: 6, opacity: 1 },
    area: { weight: 2.5, fillOpacity: 0.45 },
  };

  const base = baseStyles[category.kind];
  const hover = hoverStyles[category.kind];

  const options = {
    onEachFeature: (feature, layer) => {
      const latlng = layer.getLatLng
        ? layer.getLatLng()
        : layer.getBounds().getCenter();
      layer.bindPopup(popupHtml(category, feature));

      places.push({ category, layer, latlng, props: feature.properties || {} });

      // Keep the highlight while a feature's popup is open; otherwise the
      // popup can swallow the mouseout and leave the feature stuck emphasized.
      const reset = () => {
        if (hovered === layer) hovered = null;
        if (!layer.isPopupOpen || !layer.isPopupOpen()) layer.setStyle(base);
      };
      layer.on("mouseover", () => {
        if (hovered && hovered !== layer) hovered.fire("mouseout");
        hovered = layer;
        layer.setStyle(hover);
      });
      layer.on("mouseout", reset);
      layer.on("popupclose", () => layer.setStyle(base));
    },
  };

  if (category.kind === "point") {
    options.pane = "pointPane";
    options.pointToLayer = (_feature, latlng) =>
      L.circleMarker(latlng, { pane: "pointPane", ...base });
  } else if (category.kind === "line") {
    options.pane = "linePane";
    options.style = base;
  } else {
    // area
    options.pane = "areaPane";
    options.style = base;
  }

  return L.geoJSON(geojson, options);
}

function addToggle(category, layer, count) {
  const label = document.createElement("label");
  label.className = "layer-toggle";
  label.style.setProperty("--switch-color", category.color);
  // Toggles are appended as each fetch resolves (non-deterministic order), so
  // lay them out with flex order: most places first (negate the count, since
  // flex order sorts ascending).
  label.style.order = String(count != null ? -count : 0);

  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = true; // all layers on by default — equal footing
  category.enabled = true;
  input.addEventListener("change", () => {
    category.enabled = input.checked;
    label.classList.toggle("off", !input.checked);
    if (input.checked) layer.addTo(map);
    else map.removeLayer(layer);
    renderNearby(); // keep the sidebar in sync with the visible layers
  });

  const emoji = document.createElement("span");
  emoji.className = "layer-emoji";
  emoji.textContent = category.icon;

  const name = document.createElement("span");
  name.className = "layer-name";
  name.textContent = category.label;

  const countEl = document.createElement("span");
  countEl.className = "layer-count";
  countEl.textContent = count != null ? count : "—";

  const sw = document.createElement("span");
  sw.className = "layer-switch";

  label.append(input, emoji, name, countEl, sw);
  els.layerToggles.appendChild(label);
}

function countFeatures(geojson) {
  if (Array.isArray(geojson.features)) return geojson.features.length;
  return geojson.type === "Feature" ? 1 : 0;
}

async function loadCategory(category) {
  try {
    const res = await fetch(category.url, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const geojson = await res.json();
    const layer = buildLayer(category, geojson);
    layer.addTo(map); // on by default
    addToggle(category, layer, countFeatures(geojson));
    renderNearby(); // refresh the sidebar as each category's features arrive
  } catch (err) {
    console.error(`Couldn't load "${category.id}":`, err);
    setStatus(`Couldn't load ${category.label.toLowerCase()}.`, 4000);
  }
}

// --- Nearby sidebar ----------------------------------------------------------

function formatDistance(meters) {
  if (meters < 950) return `${Math.round(meters / 10) * 10} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}

function focusPlace(place) {
  map.flyTo(place.latlng, Math.max(map.getZoom(), 16), { duration: 0.6 });
  place.layer.openPopup();
}

function placeListItem(place, meters) {
  const li = document.createElement("li");
  li.className = "place";
  li.tabIndex = 0;
  li.setAttribute("role", "button");

  const icon = document.createElement("span");
  icon.className = "place-icon";
  icon.textContent = place.category.icon;

  const body = document.createElement("div");
  body.className = "place-body";

  const name = document.createElement("div");
  name.className = "place-name";
  name.textContent = place.props.name;

  // The category is already shown by the icon, so the subtext is just the rating.
  const meta = document.createElement("div");
  meta.className = "place-meta";
  if (typeof place.props.rating === "number") {
    const reviews =
      place.props.reviews != null ? ` (${place.props.reviews.toLocaleString()})` : "";
    meta.innerHTML = `<span class="star">★</span> ${escapeHtml(
      place.props.rating.toFixed(1)
    )}${escapeHtml(reviews)}`;
  } else {
    meta.textContent = "No ratings yet";
  }

  body.append(name, meta);

  const dist = document.createElement("span");
  dist.className = "place-dist";
  dist.textContent = formatDistance(meters);

  li.append(icon, body, dist);
  li.addEventListener("click", () => focusPlace(place));
  li.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      focusPlace(place);
    }
  });
  return li;
}

function renderNearby() {
  if (!places.length) return;

  const ranked = places
    .filter((place) => place.category.enabled !== false) // respect layer toggles
    .map((place) => ({ place, meters: referencePoint.distanceTo(place.latlng) }))
    .sort((a, b) => a.meters - b.meters)
    .slice(0, NEARBY_LIMIT);

  const frag = document.createDocumentFragment();
  for (const { place, meters } of ranked) {
    frag.appendChild(placeListItem(place, meters));
  }
  els.nearbyList.replaceChildren(frag);

  if (!ranked.length) {
    const empty = document.createElement("li");
    empty.className = "nearby-empty";
    empty.textContent = "No places to show — turn on a layer.";
    els.nearbyList.replaceChildren(empty);
  }
}

function setupToolbarToggle() {
  els.toolbarToggle.addEventListener("click", () => {
    const collapsed = els.toolbar.classList.toggle("collapsed");
    els.toolbarToggle.setAttribute("aria-expanded", String(!collapsed));
    els.toolbarToggle.title = collapsed ? "Expand filters" : "Collapse filters";
  });
}

// --- Origin (the point the sidebar ranks distances from) ---------------------

// A red teardrop pin that points exactly at its coordinate (anchored at the
// tip). Marks the current "near you" origin, whether typed or located.
const PIN_SVG =
  '<svg class="origin-pin" viewBox="0 0 24 36" width="28" height="42" aria-hidden="true">' +
  '<path d="M12 0a12 12 0 0 0-12 12c0 8.5 12 24 12 24s12-15.5 12-24A12 12 0 0 0 12 0Z" fill="#e8312f" stroke="#fff" stroke-width="2"/>' +
  '<circle cx="12" cy="12" r="4.5" fill="#fff"/>' +
  "</svg>";

const ORIGIN_ICON = L.divIcon({
  className: "",
  html: PIN_SVG,
  iconSize: [28, 42],
  iconAnchor: [14, 42], // tip of the teardrop sits on the coordinate
  popupAnchor: [0, -38],
});

// Make `latlng` the new "near you" origin: re-rank the sidebar and drop the pin.
function setOrigin(latlng, label, { fly } = {}) {
  referencePoint = L.latLng(latlng[0], latlng[1]);
  els.nearbyOrigin.textContent = label;
  renderNearby();

  if (originMarker) originMarker.remove();
  originMarker = L.marker(latlng, { icon: ORIGIN_ICON, zIndexOffset: 1000 })
    .addTo(map)
    .bindPopup(label);

  if (fly) map.flyTo(latlng, 15, { duration: 0.6 });
}

// --- Geolocation -------------------------------------------------------------

// Triggered only by the "use my location" button — the default view stays on
// central Singapore until the user opts in.
function locateUser() {
  if (!navigator.geolocation) {
    setStatus("Geolocation isn't supported by your browser.");
    return;
  }

  els.locate.disabled = true;
  setStatus("Locating you…");

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      els.locate.disabled = false;
      setOrigin([pos.coords.latitude, pos.coords.longitude], "Your location", {
        fly: true,
      });
      setStatus("📍 Found you — centering the map", 2500);
    },
    (err) => {
      els.locate.disabled = false;
      setStatus(
        err.code === err.PERMISSION_DENIED
          ? "Location permission denied."
          : "Couldn't get your location."
      );
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
  );
}

// --- Address / postal code search --------------------------------------------

// Geocode via OneMap, Singapore's official basemap service — its search handles
// postal codes, building names and street addresses, and needs no API key.
async function geocodeAddress(query) {
  const url =
    "https://www.onemap.gov.sg/api/common/elastic/search" +
    `?searchVal=${encodeURIComponent(query)}&returnGeom=Y&getAddrDetails=Y&pageNum=1`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  const hit = data.results && data.results[0];
  if (!hit) return null;

  const lat = parseFloat(hit.LATITUDE);
  const lng = parseFloat(hit.LONGITUDE);
  if (Number.isNaN(lat) || Number.isNaN(lng)) return null;
  return { latlng: [lat, lng], label: hit.SEARCHVAL || query };
}

async function searchAddress(event) {
  event.preventDefault();
  const query = els.addressInput.value.trim();
  if (!query) return;

  // Singapore postal codes are exactly six digits.
  if (!/^\d{6}$/.test(query)) {
    setStatus("Enter a 6-digit postal code.", 4000);
    return;
  }

  setStatus("Searching…");
  try {
    const hit = await geocodeAddress(query);
    if (!hit) {
      setStatus("No place found for that postal code.", 4000);
      return;
    }
    setOrigin(hit.latlng, hit.label, { fly: true });
    setStatus("");
  } catch (err) {
    console.error("Postal code search failed:", err);
    setStatus("Couldn't search that postal code. Try again.", 4000);
  }
}

// --- Theme (light / dark) ----------------------------------------------------

const THEME_KEY = "movesg-theme";
// Surface tint behind the address bar etc., kept in sync with the active theme.
const THEME_COLOR = { light: "#ffffff", dark: "#0e131b" };

function applyTheme(theme) {
  const root = document.documentElement;
  // Suppress per-element transitions so the whole UI swaps theme in one frame,
  // instead of staggering as each hover transition animates its new colour.
  root.classList.add("theme-switching");
  root.setAttribute("data-theme", theme);
  void root.offsetWidth; // force a reflow before transitions are restored
  requestAnimationFrame(() => root.classList.remove("theme-switching"));

  baseLayer.setUrl(BASEMAP_URL[theme]);

  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", THEME_COLOR[theme]);

  const next = theme === "dark" ? "light" : "dark";
  els.themeToggle.setAttribute("aria-label", `Switch to ${next} theme`);
  els.themeToggle.title = `Switch to ${next} theme`;
}

function setupTheme() {
  // Saved choice wins; otherwise follow the OS preference.
  const saved = localStorage.getItem(THEME_KEY);
  const prefersDark =
    window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (prefersDark ? "dark" : "light"));

  els.themeToggle.addEventListener("click", () => {
    const next =
      document.documentElement.getAttribute("data-theme") === "dark"
        ? "light"
        : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  });
}

// --- Init --------------------------------------------------------------------

function init() {
  setupTheme();
  els.locate.addEventListener("click", () => {
    els.addressInput.value = ""; // the located point supersedes any typed code
    locateUser();
  });
  els.searchForm.addEventListener("submit", searchAddress);
  // Keep the field to postal-code characters only — digits, nothing else.
  els.addressInput.addEventListener("input", () => {
    const digits = els.addressInput.value.replace(/\D/g, "").slice(0, 6);
    if (digits !== els.addressInput.value) els.addressInput.value = digits;
  });
  setupToolbarToggle();
  for (const category of CATEGORIES) loadCategory(category);
  // Default view is central Singapore; the user opts in to geolocation.
}

init();
