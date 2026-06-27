"use strict";

// Singapore bounds / default view.
const SG_CENTER = [1.3521, 103.8198];
const DEFAULT_ZOOM = 12;

const els = {
  list: document.getElementById("list"),
  search: document.getElementById("search"),
  count: document.getElementById("count"),
  status: document.getElementById("status"),
  locate: document.getElementById("locate-btn"),
};

let courts = []; // full dataset
let userLocation = null; // [lat, lng] or null
let markers = new Map(); // court.id -> Leaflet marker
let userMarker = null;
let activeId = null;

// --- Map setup ---------------------------------------------------------------

const map = L.map("map", { zoomControl: true }).setView(SG_CENTER, DEFAULT_ZOOM);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

// --- Helpers -----------------------------------------------------------------

/** Great-circle distance in kilometres (haversine). */
function distanceKm(a, b) {
  const R = 6371;
  const dLat = ((b[0] - a[0]) * Math.PI) / 180;
  const dLng = ((b[1] - a[1]) * Math.PI) / 180;
  const lat1 = (a[0] * Math.PI) / 180;
  const lat2 = (b[0] * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function formatDistance(km) {
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(1)} km`;
}

function setStatus(message) {
  els.status.textContent = message || "";
  els.status.classList.toggle("show", Boolean(message));
}

function badgesFor(court) {
  const out = [];
  if (court.indoor === "yes") out.push("Indoor");
  if (court.access === "private") out.push("Private");
  else if (court.access === "yes" || court.access === "public") out.push("Public");
  if (court.surface) out.push(cap(court.surface));
  if (court.lit === "yes") out.push("Lit");
  if (court.hoops) out.push(`${court.hoops} hoops`);
  return out;
}

function cap(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function directionsUrl(court) {
  return `https://www.google.com/maps/dir/?api=1&destination=${court.lat},${court.lng}`;
}

// --- Rendering ---------------------------------------------------------------

function visibleCourts() {
  const query = els.search.value.trim().toLowerCase();
  let result = courts;

  if (query) {
    result = result.filter((c) => c.name.toLowerCase().includes(query));
  }

  if (userLocation) {
    result = result
      .map((c) => ({ ...c, _dist: distanceKm(userLocation, [c.lat, c.lng]) }))
      .sort((a, b) => a._dist - b._dist);
  } else {
    result = [...result].sort((a, b) => a.name.localeCompare(b.name));
  }

  return result;
}

function renderList() {
  const items = visibleCourts();
  els.count.textContent = `${items.length} court${items.length === 1 ? "" : "s"}`;
  els.list.innerHTML = "";

  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No courts match your search.";
    els.list.appendChild(empty);
    return;
  }

  const frag = document.createDocumentFragment();
  for (const court of items) {
    frag.appendChild(renderCard(court));
  }
  els.list.appendChild(frag);
}

function renderCard(court) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "court";
  card.dataset.id = court.id;
  if (court.id === activeId) card.classList.add("active");

  const dist =
    court._dist !== undefined
      ? `<span class="court-dist">${formatDistance(court._dist)}</span>`
      : "";

  const badges = badgesFor(court)
    .map((b) => `<span class="badge">${b}</span>`)
    .join("");

  card.innerHTML = `
    <div class="court-top">
      <h3 class="court-name">${escapeHtml(court.name)}</h3>
      ${dist}
    </div>
    ${badges ? `<div class="badges">${badges}</div>` : ""}
    <div class="court-actions">
      <a class="directions" href="${directionsUrl(court)}" target="_blank" rel="noopener">Directions →</a>
    </div>
  `;

  card.addEventListener("click", (event) => {
    if (event.target.closest("a")) return; // let the directions link work
    focusCourt(court.id);
  });

  return card;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// --- Markers -----------------------------------------------------------------

function buildMarkers() {
  for (const court of courts) {
    const marker = L.marker([court.lat, court.lng]).bindPopup(
      `<strong>${escapeHtml(court.name)}</strong><br />
       <a href="${directionsUrl(court)}" target="_blank" rel="noopener">Get directions</a>`
    );
    marker.on("click", () => setActive(court.id));
    marker.addTo(map);
    markers.set(court.id, marker);
  }
}

function focusCourt(id) {
  const marker = markers.get(id);
  const court = courts.find((c) => c.id === id);
  if (!marker || !court) return;
  setActive(id);
  map.flyTo([court.lat, court.lng], 17, { duration: 0.6 });
  marker.openPopup();
}

function setActive(id) {
  activeId = id;
  for (const el of els.list.querySelectorAll(".court")) {
    el.classList.toggle("active", el.dataset.id === id);
  }
  const activeEl = els.list.querySelector(".court.active");
  if (activeEl) activeEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
      userLocation = [pos.coords.latitude, pos.coords.longitude];
      els.locate.disabled = false;
      setStatus("Showing courts nearest to you.");

      if (userMarker) userMarker.remove();
      userMarker = L.marker(userLocation, {
        icon: L.divIcon({ className: "", html: '<div class="user-marker"></div>', iconSize: [16, 16] }),
        zIndexOffset: 1000,
      })
        .addTo(map)
        .bindPopup("You are here");

      map.flyTo(userLocation, 14, { duration: 0.6 });
      renderList();
    },
    (err) => {
      els.locate.disabled = false;
      const msg =
        err.code === err.PERMISSION_DENIED
          ? "Location permission denied. Showing all courts."
          : "Couldn't get your location. Showing all courts.";
      setStatus(msg);
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
  );
}

// --- Init --------------------------------------------------------------------

async function init() {
  els.locate.addEventListener("click", locateUser);
  els.search.addEventListener("input", renderList);

  try {
    const res = await fetch("data/courts.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    courts = await res.json();
  } catch (err) {
    setStatus("Couldn't load court data. Please try again later.");
    console.error(err);
    return;
  }

  buildMarkers();
  renderList();
}

init();
