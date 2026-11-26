import { useCallback, useEffect, useRef, useState } from "react";
import {
  confirmHome,
  createHome,
  createTrip,
  deleteHome,
  fetchDismissedTrips,
  fetchHomes,
  restoreTrip,
  updateHome,
} from "../api/client.js";
import { formatTripRange } from "../trips.js";

const REBUILD_LATER =
  "Another job was running, so trips were not rebuilt. Run Rebuild Trips above when it has finished.";

function describeHome(home) {
  const place = [home.city, home.state, home.country].filter(Boolean).join(", ");
  const parts = [
    place,
    `${home.latitude.toFixed(3)}, ${home.longitude.toFixed(3)}`,
    home.radius_km ? `${home.radius_km} km` : "default radius",
    home.start_date || home.end_date ? `${home.start_date ?? "always"} to ${home.end_date ?? "now"}` : "all dates",
  ];
  return parts.filter(Boolean).join(" · ");
}

const blank = { name: "", latitude: "", longitude: "", radius_km: "", start_date: "", end_date: "" };

function fromHome(home) {
  return {
    name: home.name ?? "",
    latitude: String(home.latitude),
    longitude: String(home.longitude),
    radius_km: home.radius_km == null ? "" : String(home.radius_km),
    start_date: home.start_date ?? "",
    end_date: home.end_date ?? "",
  };
}

function toPayload(draft) {
  return {
    name: draft.name.trim() || null,
    latitude: Number(draft.latitude),
    longitude: Number(draft.longitude),
    radius_km: draft.radius_km === "" ? null : Number(draft.radius_km),
    start_date: draft.start_date || null,
    end_date: draft.end_date || null,
  };
}

function HomeForm({ initial = blank, submitLabel, onSubmit, onCancel }) {
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const set = (key) => (event) => setDraft((prev) => ({ ...prev, [key]: event.target.value }));
  const valid = draft.latitude.trim() !== "" && draft.longitude.trim() !== "" && !Number.isNaN(Number(draft.latitude)) && !Number.isNaN(Number(draft.longitude));

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    try {
      await onSubmit(toPayload(draft));
      if (!onCancel) setDraft(blank);
    } catch {
      // The parent shows the error; keep what was typed so it can be fixed.
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="trips-home-form" onSubmit={submit}>
      <input aria-label="Home name" placeholder="Name (optional)" value={draft.name} onChange={set("name")} />
      <input aria-label="Latitude" placeholder="Latitude" inputMode="decimal" value={draft.latitude} onChange={set("latitude")} />
      <input aria-label="Longitude" placeholder="Longitude" inputMode="decimal" value={draft.longitude} onChange={set("longitude")} />
      <input aria-label="Radius in km" placeholder="Radius km" inputMode="decimal" value={draft.radius_km} onChange={set("radius_km")} />
      <label>
        From <input aria-label="Lived here from" type="date" value={draft.start_date} onChange={set("start_date")} />
      </label>
      <label>
        Until <input aria-label="Lived here until" type="date" value={draft.end_date} onChange={set("end_date")} />
      </label>
      <button type="submit" disabled={!valid || busy}>
        {submitLabel}
      </button>
      {onCancel && (
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      )}
    </form>
  );
}

// The Manage page's trips section: where home is (detected, then confirmed
// or entered by the user -- see trips.py), trips made by hand, and dismissed
// trips with a way to bring them back. The Rebuild Trips button itself lives
// with the other jobs above. `onTripsChanged` tells App.jsx to reload the
// sidebar's trip list.
export default function TripsManagement({ jobs, onTripsChanged = () => {} }) {
  const [homes, setHomes] = useState(null);
  const [dismissed, setDismissed] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [tripName, setTripName] = useState("");
  const [tripFrom, setTripFrom] = useState("");
  const [tripTo, setTripTo] = useState("");

  const load = useCallback(() => {
    fetchHomes()
      .then(setHomes)
      .catch((err) => setError(err.message));
    fetchDismissedTrips()
      .then(setDismissed)
      .catch((err) => setError(err.message));
  }, []);

  useEffect(load, [load]);

  // Detection replaces the detected homes on every run, so look again when a
  // trips rebuild (ours, a sync's, anyone's) has just ended.
  const running = jobs?.active === "trips";
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) load();
    wasRunning.current = running;
  }, [running, load]);

  // Runs one change, then reloads. Errors show in the section and are
  // rethrown so a form can stay open.
  async function change(action, done) {
    setError(null);
    setNotice(null);
    try {
      const result = await action();
      if (result && result.rebuild_started === false) setNotice(REBUILD_LATER);
      else if (done) setNotice(done(result));
      load();
      return result;
    } catch (err) {
      setError(err.message);
      throw err;
    }
  }

  const unconfirmed = (homes ?? []).filter((home) => !home.confirmed);

  return (
    <div className="trips-management">
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="trips-notice">
          {notice}
        </p>
      )}

      <h3>Where home is</h3>
      <p className="management-refresh-description">
        A photo counts as part of a trip when it was taken far from home. Home is worked out from where
        you spend most of your days, one place per year; confirm it, or enter your own.
      </p>

      {unconfirmed.length > 0 && (
        <div className="trips-home-prompt" role="status">
          <strong>Is this home?</strong>
          <ul>
            {unconfirmed.map((home) => (
              <li key={home.id}>{[home.name, describeHome(home)].filter(Boolean).join(" — ")}</li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => change(() => confirmHome(unconfirmed[0].id), () => "Home confirmed.").catch(() => {})}
          >
            Yes, that&apos;s home
          </button>
          <span className="trips-home-prompt-hint"> Otherwise edit or remove it below, or add your own.</span>
        </div>
      )}

      {homes === null ? (
        <p>Loading…</p>
      ) : homes.length === 0 ? (
        <p className="empty-state">
          No home found yet. Home needs about a month of photos with GPS in one place in a year -- run
          Rebuild Trips after a sync, or add one below.
        </p>
      ) : (
        <ul className="trips-homes">
          {homes.map((home) => (
            <li key={home.id} className="trips-home">
              {editingId === home.id ? (
                <HomeForm
                  initial={fromHome(home)}
                  submitLabel="Save"
                  onCancel={() => setEditingId(null)}
                  onSubmit={(fields) =>
                    change(() => updateHome(home.id, fields), () => "Home saved.").then(() => setEditingId(null))
                  }
                />
              ) : (
                <>
                  <div className="trips-home-text">
                    <span className="trips-home-name">{home.name || "Home"}</span>
                    <span className="trips-home-badge">{home.confirmed ? (home.source === "manual" ? "entered" : "confirmed") : "detected"}</span>
                    <span className="trips-home-detail">{describeHome(home)}</span>
                  </div>
                  <div className="management-tag-actions">
                    <button type="button" onClick={() => setEditingId(home.id)}>
                      Edit
                    </button>
                    <button
                      type="button"
                      className="management-delete"
                      onClick={() => change(() => deleteHome(home.id), () => "Home removed.").catch(() => {})}
                    >
                      Remove
                    </button>
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <details className="trips-add">
        <summary>Add a home</summary>
        <HomeForm
          submitLabel="Add home"
          onSubmit={(fields) => change(() => createHome(fields), () => "Home added.")}
        />
      </details>

      <h3>Make a trip by hand</h3>
      <p className="management-refresh-description">
        Picks up every photo taken in the date range that is not already in a trip, GPS or not. To
        put particular photos in a trip, right-click them in the gallery.
      </p>
      <form
        className="trips-home-form"
        onSubmit={(event) => {
          event.preventDefault();
          change(
            () => createTrip({ name: tripName, startDate: tripFrom, endDate: tripTo || tripFrom }),
            (trip) => `Made “${trip.name}” with ${trip.photo_count} photos.`,
          )
            .then(() => {
              setTripName("");
              setTripFrom("");
              setTripTo("");
              onTripsChanged();
            })
            .catch(() => {});
        }}
      >
        <input aria-label="Trip name" placeholder="Trip name" value={tripName} onChange={(event) => setTripName(event.target.value)} />
        <label>
          From <input aria-label="Trip from" type="date" value={tripFrom} onChange={(event) => setTripFrom(event.target.value)} />
        </label>
        <label>
          Until <input aria-label="Trip until" type="date" value={tripTo} onChange={(event) => setTripTo(event.target.value)} />
        </label>
        <button type="submit" disabled={!tripName.trim() || !tripFrom}>
          Make trip
        </button>
      </form>

      <h3>Dismissed trips</h3>
      {dismissed === null ? (
        <p>Loading…</p>
      ) : dismissed.length === 0 ? (
        <p className="empty-state">No dismissed trips.</p>
      ) : (
        <ul className="trips-homes">
          {dismissed.map((trip) => (
            <li key={trip.id} className="trips-home">
              <div className="trips-home-text">
                <span className="trips-home-name">{trip.name}</span>
                <span className="trips-home-detail">
                  {[formatTripRange(trip), `${trip.photo_count} photos`].filter(Boolean).join(" · ")}
                </span>
              </div>
              <div className="management-tag-actions">
                <button
                  type="button"
                  onClick={() =>
                    change(() => restoreTrip(trip.id), () => `Restored “${trip.name}”.`)
                      .then(onTripsChanged)
                      .catch(() => {})
                  }
                >
                  Restore
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
