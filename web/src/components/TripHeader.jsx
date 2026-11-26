import { useState } from "react";
import { formatTripRange } from "../trips.js";
import TripMap from "./TripMap.jsx";

// Banner above a selected trip's gallery: its name, dates and size, a
// collapsible map of where it went, and -- while one of the map's stops is
// selected -- a note and a way back to the whole trip. `locations` is null
// while it loads.
//
// The action buttons edit the trip (all of which detection then respects):
// rename (or go back to the generated name), merge into another trip, split
// at a date, or dismiss. `onRename(name)`, `onMerge(otherTripId)`,
// `onSplit(isoDate)` and `onDismiss()` return promises; a rejection's
// message is shown here and the panel stays open.
export default function TripHeader({
  trip,
  locations,
  selectedStop,
  onSelectStop,
  otherTrips = [],
  onRename,
  onMerge,
  onSplit,
  onDismiss,
}) {
  const [mapOpen, setMapOpen] = useState(true);
  const [panel, setPanel] = useState(null); // "rename" | "merge" | "split" | null
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const stop = selectedStop !== null ? locations?.stops[selectedStop] : null;
  const range = formatTripRange(trip);
  const kind = trip.kind === "outing" ? "Day outing" : null;
  const editable = Boolean(onRename);

  function open(next, initial = "") {
    setPanel(panel === next ? null : next);
    setDraft(initial);
    setError(null);
  }

  async function run(action) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setPanel(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const day = (value) => (value ? value.slice(0, 10) : undefined);

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

      {editable && (
        <div className="trip-actions">
          <button type="button" className="trip-map-toggle" onClick={() => open("rename", trip.name)} aria-expanded={panel === "rename"}>
            Rename
          </button>
          <button type="button" className="trip-map-toggle" onClick={() => open("merge", otherTrips[0]?.id ?? "")} aria-expanded={panel === "merge"} disabled={otherTrips.length === 0}>
            Merge with…
          </button>
          <button type="button" className="trip-map-toggle" onClick={() => open("split")} aria-expanded={panel === "split"} disabled={trip.photo_count < 2}>
            Split…
          </button>
          <button
            type="button"
            className="trip-map-toggle trip-dismiss"
            onClick={() => {
              if (window.confirm(`Dismiss “${trip.name}”? It disappears from the trip list and won't be detected again. You can restore it from the Manage page.`)) {
                run(onDismiss);
              }
            }}
          >
            Dismiss
          </button>
        </div>
      )}

      {panel === "rename" && (
        <form
          className="trip-panel"
          onSubmit={(event) => {
            event.preventDefault();
            run(() => onRename(draft));
          }}
        >
          <input aria-label="Trip name" value={draft} onChange={(event) => setDraft(event.target.value)} autoFocus />
          <button type="submit" disabled={busy || !draft.trim()}>
            Save
          </button>
          {trip.name_source === "manual" && (
            <button type="button" disabled={busy} onClick={() => run(() => onRename(""))}>
              Use the generated name
            </button>
          )}
          <button type="button" onClick={() => setPanel(null)}>
            Cancel
          </button>
        </form>
      )}

      {panel === "merge" && (
        <form
          className="trip-panel"
          onSubmit={(event) => {
            event.preventDefault();
            run(() => onMerge(draft));
          }}
        >
          <label>
            Merge this trip with
            <select aria-label="Trip to merge with" value={draft} onChange={(event) => setDraft(event.target.value)}>
              {otherTrips.map((other) => (
                <option key={other.id} value={other.id}>
                  {other.name}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={busy || !draft}>
            Merge
          </button>
          <button type="button" onClick={() => setPanel(null)}>
            Cancel
          </button>
          <span className="trip-panel-hint">The earlier trip keeps its place; a name you chose is kept.</span>
        </form>
      )}

      {panel === "split" && (
        <form
          className="trip-panel"
          onSubmit={(event) => {
            event.preventDefault();
            run(() => onSplit(draft));
          }}
        >
          <label>
            Photos from
            <input
              aria-label="Split from date"
              type="date"
              value={draft}
              min={day(trip.start_at)}
              max={day(trip.end_at)}
              onChange={(event) => setDraft(event.target.value)}
            />
          </label>
          <span className="trip-panel-hint">on move to a new trip.</span>
          <button type="submit" disabled={busy || !draft}>
            Split
          </button>
          <button type="button" onClick={() => setPanel(null)}>
            Cancel
          </button>
        </form>
      )}

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

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
