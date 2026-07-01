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
  sidebar: document.querySelector(".sidebar"),
  sheetHandle: document.getElementById("sheet-handle"),
  layerToggles: document.getElementById("layer-toggles"),
  toolbar: document.getElementById("toolbar"),
  toolbarToggle: document.getElementById("toolbar-toggle"),
  nearbyList: document.getElementById("nearby-list"),
  nearbyOrigin: document.getElementById("nearby-origin"),
  searchForm: document.getElementById("search-form"),
  addressInput: document.getElementById("address-input"),
  searchClear: document.getElementById("search-clear"),
  searchSuggestions: document.getElementById("search-suggestions"),
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

// A compact star rating: the numeric score and review count, plus a five-star
// glyph. Fractional fill is done by clipping a gold star row over a muted one
// (both using the ubiquitous ★ glyph) rather than a dedicated half-star
// character — exotic glyphs like ⯨ have no coverage in most mobile system
// fonts and render as a tofu box.
function ratingHtml(rating, reviews) {
  const pct = Math.max(0, Math.min(100, (rating / 5) * 100));
  const stars =
    `<span class="popup-stars" aria-hidden="true">` +
    `<span class="popup-stars-base">★★★★★</span>` +
    `<span class="popup-stars-fill" style="width:${pct}%">★★★★★</span>` +
    `</span>`;

  const count =
    reviews != null
      ? ` <span class="popup-reviews">(${reviews.toLocaleString()})</span>`
      : "";
  return `<div class="popup-rating">${stars} <span class="popup-score">${rating.toFixed(1)}</span>${count}</div>`;
}

// Build a Google Maps link that resolves to the right place whether it opens in
// a browser or hands off to the Maps app on a phone. The stored URIs come in two
// shapes — `…/maps/place/?q=place_id:…` (courts) and `…?cid=…&g_mp=…` (pools) —
// and neither is the documented universal format the app reliably parses, so we
// normalise them: a place_id goes through the official Maps URLs API, and a cid
// is reduced to the bare `?cid=` form (dropping the internal g_mp param). When
// there's no usable id we fall back to a name+address search.
function placeUrl(props) {
  const query = [props.name, props.address].filter(Boolean).join(" ");
  const search = (extra = "") =>
    `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}${extra}`;

  const uri = props.google_maps_uri || "";
  const placeId = uri.match(/place_id:([\w-]+)/);
  if (placeId) return search(`&query_place_id=${placeId[1]}`);

  const cid = uri.match(/[?&]cid=(\d+)/);
  if (cid) return `https://www.google.com/maps?cid=${cid[1]}`;

  return uri || search();
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

// Different layers can geocode to the exact same point — e.g. an ActiveSG sport
// park that holds both a swimming pool and basketball courts returns one centroid
// for both, so one marker ends up hidden directly beneath the other. Rather than
// falsify the data, we keep the coordinates intact and nudge a colliding marker a
// few metres onto a small ring around the shared point so every marker stays
// visible and clickable. The first marker at a spot keeps the true location; each
// later collision fans out in a different direction.
const placedPoints = [];
const COLLISION_RADIUS_M = 16; // markers closer than this are treated as stacked
const SPREAD_M = 22; // how far a collided marker is pushed off the shared point

function spreadColliding(latlng) {
  let collisions = 0;
  for (const p of placedPoints) {
    if (latlng.distanceTo(p) < COLLISION_RADIUS_M) collisions += 1;
  }
  // Record the true point (not the nudged one) so further collisions still count.
  placedPoints.push(latlng);
  if (collisions === 0) return latlng;

  const angle = (collisions - 1) * ((2 * Math.PI) / 5);
  const dLat = (SPREAD_M / 111320) * Math.cos(angle);
  const dLng =
    (SPREAD_M / (111320 * Math.cos((latlng.lat * Math.PI) / 180))) *
    Math.sin(angle);
  return L.latLng(latlng.lat + dLat, latlng.lng + dLng);
}

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
    options.pointToLayer = (feature, latlng) =>
      L.circleMarker(spreadColliding(latlng), {
        pane: "pointPane",
        ...base,
      });
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
  // On mobile the sheet covers the map, so collapse it to peek — otherwise a
  // list tap flies a map the user can't see.
  if (isMobile()) setSheet("peek");
  map.flyTo(place.latlng, Math.max(map.getZoom(), 16), { duration: 0.6 });
  // Anchor the popup to the same point flyTo centers on. Leaflet's default
  // openPopup() uses the layer's own centroid, which for a large MultiPolygon
  // (e.g. Central Catchment) is the first sub-polygon's center — kilometres
  // from the bounds centre and off-screen at this zoom.
  place.layer.openPopup(place.latlng);
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
    empty.textContent = "No places to show — turn on a filter.";
    els.nearbyList.replaceChildren(empty);
  }
}

function setToolbarCollapsed(collapsed) {
  els.toolbar.classList.toggle("collapsed", collapsed);
  els.toolbarToggle.setAttribute("aria-expanded", String(!collapsed));
  els.toolbarToggle.title = collapsed ? "Expand filters" : "Collapse filters";
}

function setupToolbarToggle() {
  // On phones the sidebar is short, so an expanded filter panel would push the
  // nearby list out of view — start collapsed there and let the user opt in.
  if (window.matchMedia && window.matchMedia("(max-width: 760px)").matches) {
    setToolbarCollapsed(true);
  }
  els.toolbarToggle.addEventListener("click", () => {
    setToolbarCollapsed(!els.toolbar.classList.contains("collapsed"));
  });
}

// --- Mobile bottom sheet -----------------------------------------------------

// On phones the sidebar rides over a full-screen map as a draggable sheet that
// snaps between three heights: "peek" leaves just the search bar showing so the
// map is the focus, "half" is the default, and "full" opens the whole list. On
// desktop none of this applies — the sheet styling only exists under the mobile
// media query, and every entry point here is guarded by isMobile().
const mobileMq = window.matchMedia("(max-width: 760px)");
const isMobile = () => mobileMq.matches;

const SHEET_PEEK_VISIBLE = 150; // px of the sheet kept on screen when collapsed
const SHEET_ORDER = ["peek", "half", "full"];
let sheetState = "half";
let sheetY = 0; // current translateY (px); 0 = fully open

// Snap targets as translateY offsets, derived from the sheet's live height so
// they stay correct across rotation and address-bar resize.
function sheetSnaps() {
  const h = els.sidebar.offsetHeight || 1;
  return {
    full: 0,
    half: Math.round(h * 0.5),
    peek: Math.max(0, h - SHEET_PEEK_VISIBLE),
  };
}

function setSheet(state, { animate = true } = {}) {
  if (!SHEET_ORDER.includes(state)) return;
  sheetState = state;
  sheetY = sheetSnaps()[state];
  if (!animate) els.sidebar.style.transition = "none";
  els.sidebar.style.transform = `translateY(${sheetY}px)`;
  els.sidebar.dataset.sheet = state;
  els.sheetHandle.setAttribute(
    "aria-label",
    state === "full" ? "Collapse panel" : "Expand panel"
  );
  // Restore the CSS transition once a non-animated jump has painted.
  if (!animate) requestAnimationFrame(() => (els.sidebar.style.transition = ""));
}

function cycleSheet() {
  const i = SHEET_ORDER.indexOf(sheetState);
  setSheet(SHEET_ORDER[(i + 1) % SHEET_ORDER.length]);
}

function setupSheet() {
  const handle = els.sheetHandle;
  let startPointerY = 0;
  let startSheetY = 0;
  let dragging = false;
  let moved = false;

  handle.addEventListener("pointerdown", (e) => {
    if (!isMobile()) return;
    dragging = true;
    moved = false;
    startPointerY = e.clientY;
    startSheetY = sheetY;
    els.sidebar.style.transition = "none"; // track the finger 1:1
    handle.setPointerCapture(e.pointerId);
  });

  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dy = e.clientY - startPointerY;
    if (Math.abs(dy) > 4) moved = true;
    const snaps = sheetSnaps();
    sheetY = Math.min(snaps.peek, Math.max(snaps.full, startSheetY + dy));
    els.sidebar.style.transform = `translateY(${sheetY}px)`;
  });

  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    els.sidebar.style.transition = "";
    // A tap (no real drag) just steps to the next height; a drag snaps to the
    // height it was released nearest to.
    if (!moved) {
      cycleSheet();
      return;
    }
    const snaps = sheetSnaps();
    const nearest = Object.keys(snaps).reduce((best, key) =>
      Math.abs(snaps[key] - sheetY) < Math.abs(snaps[best] - sheetY) ? key : best
    );
    setSheet(nearest);
  };

  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);

  // Opening the keyboard to search needs room for the suggestions dropdown.
  els.addressInput.addEventListener("focus", () => {
    if (isMobile() && sheetState !== "full") setSheet("full");
  });
}

// The bottom sheet covers the full width, so on mobile the attribution moves to
// the top-left to stay visible instead of hiding behind (or peeking through) the
// sheet. Desktop keeps it in its usual bottom-right corner.
function positionControls() {
  const ac = map.attributionControl;
  if (!ac) return;
  // Only move it when the corner actually needs to change. Re-setting the same
  // position re-appends the control and reshuffles its stacking against the zoom
  // control, which leaves the attribution floating off the corner on desktop.
  const want = isMobile() ? "topleft" : "bottomright";
  if (ac.getPosition() !== want) ac.setPosition(want);
}

// Keep Leaflet's canvas correct when the layout flips between the desktop split
// view and the mobile sheet, and re-snap the sheet after a rotation changes its
// height. Leaflet handles plain window resizes itself.
function handleViewportChange() {
  positionControls();
  if (isMobile()) {
    setSheet(sheetState, { animate: false });
  } else {
    // Shed any inline sheet state so the desktop flex layout is untouched.
    els.sidebar.style.transform = "";
    els.sidebar.style.transition = "";
    delete els.sidebar.dataset.sheet;
  }
  map.invalidateSize();
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

  if (fly) {
    // Reveal the map when a search or geolocation recenters it.
    if (isMobile()) setSheet("peek");
    map.flyTo(latlng, 15, { duration: 0.6 });
  }
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

// --- Location search & autocomplete ------------------------------------------

// Geocode via OneMap, Singapore's official basemap service — its search handles
// postal codes, building names and street addresses, and needs no API key.
// Returns the full result list so the same call powers both the typeahead
// dropdown and the form submit.
async function geocodeSuggest(query) {
  const url =
    "https://www.onemap.gov.sg/api/common/elastic/search" +
    `?searchVal=${encodeURIComponent(query)}&returnGeom=Y&getAddrDetails=Y&pageNum=1`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return (data.results || [])
    .map((r) => {
      const lat = parseFloat(r.LATITUDE);
      const lng = parseFloat(r.LONGITUDE);
      if (Number.isNaN(lat) || Number.isNaN(lng)) return null;
      const title = r.SEARCHVAL || r.BUILDING || query;
      // ADDRESS is the fuller line; drop it when it just echoes the title.
      const sub = r.ADDRESS && r.ADDRESS !== title ? r.ADDRESS : "";
      return { latlng: [lat, lng], title, sub };
    })
    .filter(Boolean);
}

// Autocomplete state. `suggestSeq` guards against out-of-order responses, since
// a fast typist can have several lookups in flight at once.
let suggestItems = [];
let activeSuggest = -1;
let suggestSeq = 0;
let suggestDebounce = null;

function closeSuggestions() {
  // Cancel any pending or in-flight lookup so a late response can't re-open the
  // list after it's been closed (e.g. by submitting or picking a suggestion).
  clearTimeout(suggestDebounce);
  suggestSeq++;

  els.searchSuggestions.hidden = true;
  els.searchSuggestions.innerHTML = "";
  suggestItems = [];
  activeSuggest = -1;
  els.addressInput.setAttribute("aria-expanded", "false");
  els.addressInput.removeAttribute("aria-activedescendant");
}

function renderSuggestions(items) {
  suggestItems = items;
  activeSuggest = -1;
  const list = els.searchSuggestions;
  list.innerHTML = "";
  if (!items.length) {
    closeSuggestions();
    return;
  }

  items.forEach((item, i) => {
    const li = document.createElement("li");
    li.className = "search-suggestion";
    li.id = `search-suggestion-${i}`;
    li.setAttribute("role", "option");

    const title = document.createElement("span");
    title.className = "search-suggestion-title";
    title.textContent = item.title;
    li.appendChild(title);

    if (item.sub) {
      const sub = document.createElement("span");
      sub.className = "search-suggestion-sub";
      sub.textContent = item.sub;
      li.appendChild(sub);
    }

    // mousedown (not click) so it lands before the input's blur closes the list.
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      chooseSuggestion(i);
    });
    list.appendChild(li);
  });

  list.hidden = false;
  els.addressInput.setAttribute("aria-expanded", "true");
}

function moveActive(delta) {
  const n = suggestItems.length;
  if (!n) return;
  activeSuggest = (activeSuggest + delta + n) % n;
  const nodes = els.searchSuggestions.children;
  for (let i = 0; i < nodes.length; i++) {
    nodes[i].classList.toggle("is-active", i === activeSuggest);
  }
  const active = nodes[activeSuggest];
  if (active) {
    active.scrollIntoView({ block: "nearest" });
    els.addressInput.setAttribute("aria-activedescendant", active.id);
  }
}

function chooseSuggestion(i) {
  const item = suggestItems[i];
  if (!item) return;
  els.addressInput.value = item.title;
  els.searchClear.hidden = false;
  closeSuggestions();
  setOrigin(item.latlng, item.title, { fly: true });
  setStatus("");
}

async function runSuggest(query) {
  const seq = ++suggestSeq;
  try {
    const items = await geocodeSuggest(query);
    if (seq !== suggestSeq) return; // a newer keystroke superseded this lookup
    renderSuggestions(items);
  } catch (err) {
    console.error("Suggestion lookup failed:", err);
  }
}

function onSearchInput() {
  const query = els.addressInput.value.trim();
  els.searchClear.hidden = query.length === 0;
  clearTimeout(suggestDebounce);
  if (query.length < 2) {
    closeSuggestions();
    return;
  }
  suggestDebounce = setTimeout(() => runSuggest(query), 250);
}

function onSearchKeydown(e) {
  if (els.searchSuggestions.hidden || !suggestItems.length) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    moveActive(1);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    moveActive(-1);
  } else if (e.key === "Enter" && activeSuggest >= 0) {
    e.preventDefault();
    chooseSuggestion(activeSuggest);
  } else if (e.key === "Escape") {
    closeSuggestions();
  }
}

async function searchAddress(event) {
  event.preventDefault();

  // A highlighted suggestion wins — submit just confirms it.
  if (activeSuggest >= 0 && suggestItems[activeSuggest]) {
    chooseSuggestion(activeSuggest);
    return;
  }

  const query = els.addressInput.value.trim();
  if (!query) return;

  setStatus("Searching…");
  try {
    const hits = await geocodeSuggest(query);
    if (!hits.length) {
      setStatus("No place found. Try a postal code, street or building name.", 4000);
      return;
    }
    const hit = hits[0];
    els.addressInput.value = hit.title;
    closeSuggestions();
    setOrigin(hit.latlng, hit.title, { fly: true });
    setStatus("");
  } catch (err) {
    console.error("Location search failed:", err);
    setStatus("Couldn't search that location. Try again.", 4000);
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
    // The located point supersedes any typed query.
    els.addressInput.value = "";
    els.searchClear.hidden = true;
    closeSuggestions();
    locateUser();
  });
  els.searchForm.addEventListener("submit", searchAddress);
  els.addressInput.addEventListener("input", onSearchInput);
  els.addressInput.addEventListener("keydown", onSearchKeydown);
  // Close the dropdown on blur, but after a beat so a suggestion click registers.
  els.addressInput.addEventListener("blur", () => setTimeout(closeSuggestions, 120));
  els.searchClear.addEventListener("click", () => {
    els.addressInput.value = "";
    els.searchClear.hidden = true;
    closeSuggestions();
    els.addressInput.focus();
  });
  setupToolbarToggle();
  setupSheet();
  positionControls();
  if (isMobile()) setSheet("half", { animate: false });

  // Re-sync the sheet and map when the layout crosses the mobile breakpoint, and
  // re-snap + repaint the map after an orientation change (mobile browsers can
  // report a stale size on the immediate event, so give it a beat).
  const onBreakpoint = () => handleViewportChange();
  if (mobileMq.addEventListener) mobileMq.addEventListener("change", onBreakpoint);
  else if (mobileMq.addListener) mobileMq.addListener(onBreakpoint); // older Safari
  window.addEventListener("orientationchange", () => {
    setTimeout(() => {
      if (isMobile()) setSheet(sheetState, { animate: false });
      map.invalidateSize();
    }, 300);
  });

  for (const category of CATEGORIES) loadCategory(category);
  // Default view is central Singapore; the user opts in to geolocation.
}

init();
