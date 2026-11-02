import { useEffect, useRef, useState } from "react";
import {
  createIndividual,
  deleteIndividual,
  faceThumbnailUrl,
  fetchDescriptionRefreshStatus,
  fetchFaceRefreshStatus,
  fetchIndividualFaces,
  fetchIndividuals,
  individualThumbnailUrl,
  purgeDatabase,
  refreshDescriptions,
  refreshFaces,
  untagFace,
  updateIndividual,
} from "../api/client.js";
import SyncStatus from "./SyncStatus.jsx";

const REFRESH_POLL_INTERVAL_MS = 1000;
const REFRESH_DONE_DISPLAY_MS = 2000;

export default function ManagementPage({ syncStatus, onRefresh, onBack, onNavigateToPhoto, onPurged }) {
  const [individuals, setIndividuals] = useState(null);
  const [pageError, setPageError] = useState(null);
  const [newName, setNewName] = useState("");
  const [addError, setAddError] = useState(null);
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  // Keyed by individual id; a key's absence means "not fetched yet" for
  // that individual (distinct from an empty array, "fetched, no tagged
  // faces") -- fetched lazily, the first time that individual's accordion
  // is expanded, same pattern as PhotoModal.jsx's faceBoxes/individuals.
  const [facesByIndividual, setFacesByIndividual] = useState({});
  const [renamingId, setRenamingId] = useState(null);
  const [renameDraft, setRenameDraft] = useState("");
  // "Refresh Faces" (recognition.py's auto-matching job) -- null means
  // idle/no job ever started this page visit. Polling mirrors App.jsx's
  // pollSyncStatus/SyncStatus pattern, scoped locally to this page instead
  // of global app state, since it's specific to the individuals catalog.
  const [refreshStatus, setRefreshStatus] = useState(null);
  const refreshPollRef = useRef(null);
  // "Refresh Descriptions" (enrich.py's bulk enrich_description job) --
  // same shape as refreshStatus/refreshPollRef above, but its own state
  // since the two jobs are independent and can run/poll concurrently.
  const [descriptionRefreshStatus, setDescriptionRefreshStatus] = useState(null);
  const descriptionRefreshPollRef = useRef(null);
  const [purgeError, setPurgeError] = useState(null);

  useEffect(() => {
    fetchIndividuals()
      .then(setIndividuals)
      .catch((err) => setPageError(err.message));
    // A sync may already have auto-triggered "Refresh Faces" (see sync.py's
    // _trigger_face_refresh) before this page was ever opened -- same
    // "resume polling if it's already running" check App.jsx does for sync
    // itself on mount.
    fetchFaceRefreshStatus()
      .then((status) => {
        if (status.status === "running") {
          setRefreshStatus(status);
          pollRefreshStatus();
        }
      })
      .catch(() => {});
    // Same "resume polling if already running" check, for a description
    // refresh left running from before this page was (re)opened -- unlike
    // face refresh, nothing auto-triggers this one server-side, but it can
    // still outlive a page navigation since it's a background job either way.
    fetchDescriptionRefreshStatus()
      .then((status) => {
        if (status.status === "running") {
          setDescriptionRefreshStatus(status);
          pollDescriptionRefreshStatus();
        }
      })
      .catch(() => {});
    return () => {
      clearInterval(refreshPollRef.current);
      clearInterval(descriptionRefreshPollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A successful sync auto-triggers "Refresh Faces" server-side (see
  // sync.py's _trigger_face_refresh) -- pick up and display its progress
  // here too, not just when the standalone button below is clicked
  // directly, so "Refresh" alone shows the whole picture end to end.
  useEffect(() => {
    if (syncStatus?.status !== "completed") return;
    fetchFaceRefreshStatus()
      .then((status) => {
        setRefreshStatus(status);
        if (status.status === "running") pollRefreshStatus();
        else if (status.status === "completed") reloadAfterRefresh();
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncStatus]);

  function sortByName(list) {
    return [...list].sort((a, b) => a.name.localeCompare(b.name));
  }

  function toggleExpanded(personId) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(personId)) {
        next.delete(personId);
      } else {
        next.add(personId);
        if (!(personId in facesByIndividual)) {
          fetchIndividualFaces(personId)
            .then((faces) => setFacesByIndividual((prevFaces) => ({ ...prevFaces, [personId]: faces })))
            .catch((err) => setPageError(err.message));
        }
      }
      return next;
    });
  }

  // Re-fetches anything "Refresh Faces" may have changed: the catalog
  // (new representative thumbnails) and every accordion that's ever been
  // expanded (new auto-matched faces for it). Collapsed-and-never-opened
  // accordions need nothing -- they'll fetch fresh on first expand anyway.
  function reloadAfterRefresh() {
    fetchIndividuals()
      .then(setIndividuals)
      .catch((err) => setPageError(err.message));
    Object.keys(facesByIndividual).forEach((personId) => {
      fetchIndividualFaces(personId)
        .then((faces) => setFacesByIndividual((prev) => ({ ...prev, [personId]: faces })))
        .catch((err) => setPageError(err.message));
    });
  }

  function pollRefreshStatus() {
    clearInterval(refreshPollRef.current);
    refreshPollRef.current = setInterval(async () => {
      try {
        const status = await fetchFaceRefreshStatus();
        if (status.status === "running") {
          setRefreshStatus(status);
          return;
        }
        clearInterval(refreshPollRef.current);
        setRefreshStatus(status);
        if (status.status === "completed") reloadAfterRefresh();
        setTimeout(() => setRefreshStatus(null), REFRESH_DONE_DISPLAY_MS);
      } catch {
        clearInterval(refreshPollRef.current);
        setRefreshStatus(null);
      }
    }, REFRESH_POLL_INTERVAL_MS);
  }

  function pollDescriptionRefreshStatus() {
    clearInterval(descriptionRefreshPollRef.current);
    descriptionRefreshPollRef.current = setInterval(async () => {
      try {
        const status = await fetchDescriptionRefreshStatus();
        if (status.status === "running") {
          setDescriptionRefreshStatus(status);
          return;
        }
        clearInterval(descriptionRefreshPollRef.current);
        setDescriptionRefreshStatus(status);
        // The regenerated text itself is only visible on a photo's own
        // modal (App.jsx's photos list), not anywhere on this page -- no
        // reload here, unlike reloadAfterRefresh() for faces.
        setTimeout(() => setDescriptionRefreshStatus(null), REFRESH_DONE_DISPLAY_MS);
      } catch {
        clearInterval(descriptionRefreshPollRef.current);
        setDescriptionRefreshStatus(null);
      }
    }, REFRESH_POLL_INTERVAL_MS);
  }

  function handleRefreshDescriptions() {
    refreshDescriptions()
      .then((status) => {
        setDescriptionRefreshStatus(status);
        pollDescriptionRefreshStatus();
      })
      .catch((err) => setPageError(err.message));
  }

  function handleRefreshFaces() {
    refreshFaces()
      .then((status) => {
        setRefreshStatus(status);
        pollRefreshStatus();
      })
      .catch((err) => setPageError(err.message));
  }

  function handleAdd(event) {
    event.preventDefault();
    const name = newName.trim();
    if (!name) return;
    createIndividual(name)
      .then((created) => {
        setIndividuals((prev) => sortByName([...(prev ?? []), created]));
        setNewName("");
        setAddError(null);
      })
      .catch((err) => setAddError(err.message));
  }

  function startRename(individual) {
    setRenamingId(individual.id);
    setRenameDraft(individual.name);
  }

  function cancelRename() {
    setRenamingId(null);
    setRenameDraft("");
  }

  function saveRename(personId) {
    const name = renameDraft.trim();
    if (!name) return;
    updateIndividual(personId, name)
      .then((updated) => {
        setIndividuals((prev) => sortByName(prev.map((individual) => (individual.id === personId ? updated : individual))));
        setPageError(null);
        cancelRename();
      })
      .catch((err) => setPageError(err.message));
  }

  function handleDelete(personId) {
    deleteIndividual(personId)
      .then(() => {
        setIndividuals((prev) => prev.filter((individual) => individual.id !== personId));
        setFacesByIndividual((prev) => {
          const next = { ...prev };
          delete next[personId];
          return next;
        });
        setExpandedIds((prev) => {
          const next = new Set(prev);
          next.delete(personId);
          return next;
        });
        setPageError(null);
      })
      .catch((err) => setPageError(err.message));
  }

  function handleUntag(personId, faceId) {
    untagFace(faceId)
      .then(() => {
        setFacesByIndividual((prev) => ({
          ...prev,
          [personId]: (prev[personId] ?? []).filter((face) => face.id !== faceId),
        }));
        setPageError(null);
      })
      .catch((err) => setPageError(err.message));
  }

  // Purges the whole database (photos, faces, individuals) -- irreversible
  // and global, unlike the per-item Delete buttons above, so it's the one
  // destructive action here that confirms first.
  function handlePurge() {
    if (
      !window.confirm(
        "This permanently deletes every photo, face, and individual from the database. " +
          "The original files on the photos backend are untouched and will be re-indexed " +
          "on the next sync. This cannot be undone. Continue?"
      )
    ) {
      return;
    }
    purgeDatabase()
      .then(() => {
        setIndividuals([]);
        setFacesByIndividual({});
        setExpandedIds(new Set());
        setPurgeError(null);
        onPurged();
      })
      .catch((err) => setPurgeError(err.message));
  }

  return (
    <div className="management-page">
      <button type="button" className="management-back" onClick={onBack}>
        &larr; Back to gallery
      </button>
      <h2>Manage individuals</h2>

      <div className="management-refresh">
        <SyncStatus status={syncStatus} onRefresh={onRefresh} />
      </div>

      <div className="management-refresh-faces">
        {!refreshStatus || refreshStatus.status === "idle" ? (
          <button type="button" className="refresh-button" onClick={handleRefreshFaces}>
            Refresh Faces
          </button>
        ) : refreshStatus.status === "error" ? (
          <div className="sync-status sync-status-error" role="status">
            Refresh failed{refreshStatus.error ? `: ${refreshStatus.error}` : ""}
          </div>
        ) : (
          <div className="sync-status" role="status" aria-live="polite">
            <span className="sync-status-label">
              {refreshStatus.status === "completed"
                ? `Done -- ${refreshStatus.matched} matched`
                : `Matching faces… ${
                    refreshStatus.total > 0 ? Math.round((refreshStatus.processed / refreshStatus.total) * 100) : 0
                  }%`}
            </span>
            <div className="sync-progress-track">
              <div
                className="sync-progress-fill"
                style={{
                  width: `${
                    refreshStatus.total > 0 ? Math.round((refreshStatus.processed / refreshStatus.total) * 100) : 0
                  }%`,
                }}
              />
            </div>
          </div>
        )}
      </div>

      <div className="management-refresh-descriptions">
        {!descriptionRefreshStatus || descriptionRefreshStatus.status === "idle" ? (
          <button type="button" className="refresh-button" onClick={handleRefreshDescriptions}>
            Refresh Descriptions
          </button>
        ) : descriptionRefreshStatus.status === "error" ? (
          <div className="sync-status sync-status-error" role="status">
            Refresh failed{descriptionRefreshStatus.error ? `: ${descriptionRefreshStatus.error}` : ""}
          </div>
        ) : (
          <div className="sync-status" role="status" aria-live="polite">
            <span className="sync-status-label">
              {descriptionRefreshStatus.status === "completed"
                ? `Done -- ${descriptionRefreshStatus.updated} updated`
                : `Refreshing descriptions… ${
                    descriptionRefreshStatus.total > 0
                      ? Math.round((descriptionRefreshStatus.processed / descriptionRefreshStatus.total) * 100)
                      : 0
                  }%`}
            </span>
            <div className="sync-progress-track">
              <div
                className="sync-progress-fill"
                style={{
                  width: `${
                    descriptionRefreshStatus.total > 0
                      ? Math.round((descriptionRefreshStatus.processed / descriptionRefreshStatus.total) * 100)
                      : 0
                  }%`,
                }}
              />
            </div>
          </div>
        )}
      </div>

      {pageError && (
        <p role="alert" className="error">
          {pageError}
        </p>
      )}

      <form className="management-add-person" onSubmit={handleAdd}>
        <input
          type="text"
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          placeholder="Add a person…"
        />
        <button type="submit" disabled={!newName.trim()}>
          Add
        </button>
      </form>
      {addError && (
        <p role="alert" className="error">
          {addError}
        </p>
      )}

      {individuals === null ? (
        <p>Loading…</p>
      ) : individuals.length === 0 ? (
        <p className="empty-state">No individuals catalogued yet.</p>
      ) : (
        <ul className="management-individuals">
          {individuals.map((individual) => {
            const expanded = expandedIds.has(individual.id);
            const faces = facesByIndividual[individual.id];
            const renaming = renamingId === individual.id;

            return (
              <li key={individual.id} className="management-individual">
                <div className="management-individual-header">
                  <button
                    type="button"
                    className="management-individual-chevron"
                    onClick={() => toggleExpanded(individual.id)}
                    aria-label={expanded ? "Collapse" : "Expand"}
                    aria-expanded={expanded}
                  >
                    {expanded ? "▾" : "▸"}
                  </button>

                  {individual.has_representative_thumbnail && (
                    <img
                      className="management-individual-avatar"
                      src={individualThumbnailUrl(individual.id)}
                      alt=""
                    />
                  )}

                  {renaming ? (
                    <input
                      className="management-rename-input"
                      value={renameDraft}
                      autoFocus
                      onChange={(event) => setRenameDraft(event.target.value)}
                    />
                  ) : (
                    <span className="management-individual-name">{individual.name}</span>
                  )}

                  <span className="management-individual-actions">
                    {renaming ? (
                      <>
                        <button type="button" onClick={() => saveRename(individual.id)} disabled={!renameDraft.trim()}>
                          Save
                        </button>
                        <button type="button" onClick={cancelRename}>
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button type="button" onClick={() => startRename(individual)}>
                          Rename
                        </button>
                        <button type="button" className="management-delete" onClick={() => handleDelete(individual.id)}>
                          Delete
                        </button>
                      </>
                    )}
                  </span>
                </div>

                {expanded && (
                  <div className="management-individual-faces">
                    {faces === undefined ? (
                      <p>Loading…</p>
                    ) : faces.length === 0 ? (
                      <p className="empty-state">No faces tagged to this person yet.</p>
                    ) : (
                      faces.map((face) => (
                        <div key={face.id} className="management-face">
                          <button
                            type="button"
                            className="management-face-image"
                            onClick={() => onNavigateToPhoto(face.photo_id)}
                            title="Go to the photo this face was tagged from"
                          >
                            <img src={faceThumbnailUrl(face.id)} alt="" />
                          </button>
                          {face.tag_source === "auto" && (
                            <span className="management-face-auto-badge">auto</span>
                          )}
                          <button
                            type="button"
                            className="management-face-remove"
                            onClick={() => handleUntag(individual.id, face.id)}
                            aria-label="Remove this tag"
                          >
                            ×
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <div className="management-danger-zone">
        <h3>Danger zone</h3>
        <p>
          Permanently deletes every photo, face, and individual from the database. The original
          files on the photos backend are untouched and will be re-indexed on the next sync.
        </p>
        <button type="button" className="management-purge" onClick={handlePurge}>
          Purge database
        </button>
        {purgeError && (
          <p role="alert" className="error">
            {purgeError}
          </p>
        )}
      </div>
    </div>
  );
}
