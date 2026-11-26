import { useState } from "react";
import { groupTripsByYear } from "../trips.js";

// The "By Trip" section's body: a "Show day outings" toggle, then trips
// grouped by the year they started (newest first), each year collapsible.
// `trips` is already filtered by the toggle (see trips.js's visibleTrips).
export default function TripTree({ trips, selectedTrip, onSelectTrip, showOutings, onToggleOutings, hasTrips }) {
  const years = groupTripsByYear(trips);

  return (
    <>
      <label className="trip-outings-toggle">
        <input type="checkbox" checked={showOutings} onChange={(event) => onToggleOutings(event.target.checked)} />
        Show day outings
      </label>
      {years.length === 0 ? (
        <p className="sidebar-individuals-empty">
          {hasTrips ? "No trips yet -- day outings are hidden." : "No trips found yet."}
        </p>
      ) : (
        <ul className="directory-tree">
          {years.map((group) => (
            <TripYear key={group.year ?? "undated"} group={group} selectedTrip={selectedTrip} onSelectTrip={onSelectTrip} />
          ))}
        </ul>
      )}
    </>
  );
}

function TripYear({ group, selectedTrip, onSelectTrip }) {
  // Open by default when it holds the selected trip, so a selection is never hidden.
  const [expanded, setExpanded] = useState(group.trips.some((trip) => trip.id === selectedTrip));
  const label = group.year ?? "Undated";

  return (
    <li>
      <div className="directory-tree-item" data-count={group.count > 0 ? group.count.toLocaleString() : undefined}>
        <button
          type="button"
          className="directory-tree-toggle"
          onClick={() => setExpanded((prev) => !prev)}
          aria-label={`${expanded ? "Collapse" : "Expand"} ${label}`}
          aria-expanded={expanded}
        >
          ›
        </button>
        <button type="button" className="directory-tree-label" onClick={() => setExpanded((prev) => !prev)}>
          {label}
        </button>
      </div>
      {expanded && (
        <ul className="directory-tree">
          {group.trips.map((trip) => (
            <li key={trip.id}>
              <div
                className={`directory-tree-item${selectedTrip === trip.id ? " directory-tree-item-active" : ""}`}
                data-count={trip.photo_count > 0 ? trip.photo_count.toLocaleString() : undefined}
              >
                <span className="directory-tree-toggle-spacer" aria-hidden="true" />
                <button type="button" className="directory-tree-label" onClick={() => onSelectTrip(trip.id)}>
                  {trip.name}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
