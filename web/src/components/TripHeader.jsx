import { useState } from "react";
import { formatTripRange } from "../trips.js";
import TripMap from "./TripMap.jsx";

// Banner above a selected trip's gallery: its name, dates and size, a
// collapsible map of where it went, and -- while one of the map's stops is
// selected -- a note and a way back to the whole trip. `locations` is null
// while it loads.
export default function TripHeader({ trip, locations, selectedStop, onSelectStop }) {
  const [mapOpen, setMapOpen] = useState(true);
  const stop = selectedStop !== null ? locations?.stops[selectedStop] : null;
  const range = formatTripRange(trip);
  const kind = trip.kind === "outing" ? "Day outing" : null;

  return (
    <section className="trip-header" aria-label={`Trip: ${trip.name}`}>
      <div className="trip-header-title">
        <div>
          <h2>{trip.name}</h2>
          <p className="trip-header-meta">
            {[range, `${trip.photo_count.toLocaleString()} ${trip.photo_count === 1 ? "photo" : "photos"}`, kind]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <button type="button" className="trip-map-toggle" onClick={() => setMapOpen((prev) => !prev)} aria-expanded={mapOpen}>
          {mapOpen ? "Hide map" : "Show map"}
        </button>
      </div>

      {mapOpen &&
        (locations ? (
          <TripMap locations={locations} selectedStop={selectedStop} onSelectStop={onSelectStop} />
        ) : (
          <div className="photo-map-empty" role="status">
            Loading map…
          </div>
        ))}

      {mapOpen && locations?.unlocated_count > 0 && (
        <p className="trip-header-note">
          {locations.unlocated_count.toLocaleString()} {locations.unlocated_count === 1 ? "photo has" : "photos have"} no
          location, so {locations.unlocated_count === 1 ? "isn't" : "aren't"} on the map.
        </p>
      )}

      {stop && (
        <p className="trip-header-note trip-stop-filter">
          Showing {stop.count.toLocaleString()} {stop.count === 1 ? "photo" : "photos"} at {stop.place ?? `stop ${selectedStop + 1}`}.{" "}
          <button type="button" className="trip-stop-clear" onClick={() => onSelectStop(null)}>
            Show the whole trip
          </button>
        </p>
      )}
    </section>
  );
}
