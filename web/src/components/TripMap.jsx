import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { thumbnailUrl } from "../api/client.js";

// PhotoMap.jsx wires Leaflet's default marker assets at module load; these
// markers are divIcons styled in styles.css, so none are needed here.

const POPUP_THUMBNAILS = 4;

function formatWhen(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  if (!match) return "";
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// Built from DOM nodes with textContent, never an HTML string: a place name
// is whatever OpenStreetMap says it is.
function popupFor(stop, order) {
  const root = document.createElement("div");
  root.className = "trip-stop-popup";

  const title = document.createElement("strong");
  title.textContent = stop.place ?? `Stop ${order}`;
  root.appendChild(title);

  const first = formatWhen(stop.first_at);
  const last = formatWhen(stop.last_at);
  const meta = document.createElement("div");
  meta.className = "trip-stop-popup-meta";
  meta.textContent = `${stop.count} ${stop.count === 1 ? "photo" : "photos"} · ${first === last ? first : `${first} – ${last}`}`;
  root.appendChild(meta);

  const strip = document.createElement("div");
  strip.className = "trip-stop-popup-thumbs";
  for (const id of stop.photo_ids.slice(0, POPUP_THUMBNAILS)) {
    const img = document.createElement("img");
    img.src = thumbnailUrl(id);
    img.alt = "";
    img.loading = "lazy";
    strip.appendChild(img);
  }
  root.appendChild(strip);
  return root;
}

// The trip's map: one numbered marker per stop (photos within a couple of
// kilometres of each other), a dashed line through them in the order they
// were visited, and a home marker for context. Clicking a stop selects it
// (the gallery narrows to its photos); clicking it again clears that.
// `locations` is GET /api/trips/{id}/locations.
export default function TripMap({ locations, selectedStop, onSelectStop }) {
  const containerRef = useRef(null);
  const markersRef = useRef([]);
  const onSelectRef = useRef(onSelectStop);
  const selectedRef = useRef(selectedStop);
  onSelectRef.current = onSelectStop;
  selectedRef.current = selectedStop;

  const hasStops = locations.stops.length > 0;

  useEffect(() => {
    if (!hasStops || !containerRef.current) return undefined;

    const map = L.map(containerRef.current, { scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);

    const points = locations.stops.map((stop) => [stop.latitude, stop.longitude]);

    // A visit order may revisit a stop; the line follows every visit.
    if (locations.route.length > 1) {
      L.polyline(
        locations.route.map((index) => points[index]),
        { className: "trip-route", weight: 3, dashArray: "6 8", interactive: false },
      ).addTo(map);
    }

    if (locations.home) {
      L.marker([locations.home.latitude, locations.home.longitude], {
        icon: L.divIcon({ className: "trip-home-marker", html: "⌂", iconSize: [26, 26] }),
        title: `Home – ${locations.home.name}`,
        keyboard: false,
      }).addTo(map);
    }

    markersRef.current = locations.stops.map((stop, index) => {
      const order = index + 1;
      const marker = L.marker(points[index], {
        icon: L.divIcon({
          className: `trip-stop-marker${selectedRef.current === index ? " trip-stop-selected" : ""}`,
          html: String(order),
          iconSize: [30, 30],
        }),
        title: stop.place ?? `Stop ${order}`,
      });
      marker.bindPopup(popupFor(stop, order));
      marker.on("click", () => onSelectRef.current(selectedRef.current === index ? null : index));
      marker.addTo(map);
      return marker;
    });

    map.fitBounds(L.latLngBounds(points), { padding: [36, 36], maxZoom: 14 });

    return () => {
      markersRef.current = [];
      map.remove();
    };
  }, [locations, hasStops]);

  // Highlighting a stop only toggles a class -- rebuilding the map would
  // throw away wherever the user had panned and zoomed to.
  useEffect(() => {
    markersRef.current.forEach((marker, index) => {
      marker.getElement()?.classList.toggle("trip-stop-selected", index === selectedStop);
    });
  }, [selectedStop, locations]);

  if (!hasStops) {
    return (
      <div className="photo-map-empty" role="status">
        No location data for this trip.
      </div>
    );
  }

  return <div className="photo-map trip-map" ref={containerRef} />;
}
