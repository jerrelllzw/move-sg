"use strict";

const SG_CENTER = [1.3521, 103.8198];
const DEFAULT_ZOOM = 12;

// Every category is an equal, toggleable layer. Order = draw order on the map
// (areas first, then lines, then point markers on top).
const CATEGORIES = [
  {
    id: "parks",
    label: "Parks",
    icon: "🏞️",
    color: "#2ecc71",
    url: "data/parks.geojson",
    kind: "area",
  },
  {
    id: "pcn",
    label: "Park Connectors",
    icon: "🌳",
    color: "#00b894",
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
};

let userMarker = null;

// --- Map setup ---------------------------------------------------------------

const map = L.map("map", { zoomControl: true }).setView(SG_CENTER, DEFAULT_ZOOM);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

// Layers load concurrently, so use panes to fix stacking regardless of which
// finishes first: point markers sit above lines, which sit above area fills.
map.createPane("areaPane").style.zIndex = 410;
map.createPane("linePane").style.zIndex = 420;
map.createPane("pointPane").style.zIndex = 430;

// --- Helpers -----------------------------------------------------------------

function setStatus(message) {
  els.status.textContent = message || "";
  els.status.classList.toggle("show", Boolean(message));
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function directionsUrl(lat, lng) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
}

function popupHtml(category, feature, latlng) {
  const props = feature.properties || {};
  const name = props.name || `${category.label}`;
  let html = `<strong>${escapeHtml(name)}</strong>`;
  html += `<br /><span class="popup-cat">${category.icon} ${category.label}</span>`;

  if (props.address) {
    html += `<br /><span class="popup-detail">${escapeHtml(props.address)}</span>`;
  }

  html += `<br /><a href="${directionsUrl(latlng.lat, latlng.lng)}" target="_blank" rel="noopener">Directions →</a>`;

  if (props.source) {
    html += `<br /><span class="popup-source">Source: ${escapeHtml(props.source)}</span>`;
  }
  return html;
}

// --- Layer building ----------------------------------------------------------

function buildLayer(category, geojson) {
  const options = {
    onEachFeature: (feature, layer) => {
      const latlng = layer.getLatLng
        ? layer.getLatLng()
        : layer.getBounds().getCenter();
      layer.bindPopup(popupHtml(category, feature, latlng));
    },
  };

  if (category.kind === "point") {
    options.pane = "pointPane";
    options.pointToLayer = (_feature, latlng) =>
      L.circleMarker(latlng, {
        pane: "pointPane",
        radius: 7,
        color: "#fff",
        weight: 1.5,
        fillColor: category.color,
        fillOpacity: 0.95,
      });
  } else if (category.kind === "line") {
    options.pane = "linePane";
    options.style = { color: category.color, weight: 3, opacity: 0.9 };
  } else {
    // area
    options.pane = "areaPane";
    options.style = {
      color: category.color,
      weight: 1,
      fillColor: category.color,
      fillOpacity: 0.25,
    };
  }

  return L.geoJSON(geojson, options);
}

function addToggle(category, layer) {
  const label = document.createElement("label");
  label.className = "layer-toggle";

  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = true; // all layers on by default — equal footing
  input.addEventListener("change", () => {
    if (input.checked) layer.addTo(map);
    else map.removeLayer(layer);
  });

  const dot = document.createElement("span");
  dot.className = "layer-dot";
  dot.style.background = category.color;

  label.append(
    input,
    dot,
    document.createTextNode(` ${category.icon} ${category.label}`)
  );
  els.layerToggles.appendChild(label);
}

async function loadCategory(category) {
  try {
    const res = await fetch(category.url, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const geojson = await res.json();
    const layer = buildLayer(category, geojson);
    layer.addTo(map); // on by default
    addToggle(category, layer);
  } catch (err) {
    console.error(`Couldn't load "${category.id}":`, err);
    setStatus(`Couldn't load ${category.label.toLowerCase()}.`);
  }
}

// --- Geolocation -------------------------------------------------------------

function locateUser() {
  if (!navigator.geolocation) {
    setStatus("Geolocation isn't supported by your browser.");
    return;
  }

  els.locate.disabled = true;
  setStatus("Locating you…");

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const loc = [pos.coords.latitude, pos.coords.longitude];
      els.locate.disabled = false;
      setStatus("");

      if (userMarker) userMarker.remove();
      userMarker = L.marker(loc, {
        icon: L.divIcon({
          className: "",
          html: '<div class="user-marker"></div>',
          iconSize: [16, 16],
        }),
        zIndexOffset: 1000,
      })
        .addTo(map)
        .bindPopup("You are here");

      map.flyTo(loc, 15, { duration: 0.6 });
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

// --- Init --------------------------------------------------------------------

function init() {
  els.locate.addEventListener("click", locateUser);
  for (const category of CATEGORIES) loadCategory(category);
}

init();
