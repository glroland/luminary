import { useEffect, useRef, useState } from "react";
import {
  createIndividual,
  createTag,
  deleteIndividual,
  deleteTag,
  downloadDuplicatesCsv,
  downloadTagCsv,
  faceThumbnailUrl,
  fetchDescriptionRefreshStatus,
  fetchFaceRefreshStatus,
  fetchIndividualFaces,
  fetchIndividuals,
  fetchLocationRefreshStatus,
  fetchTags,
  individualThumbnailUrl,
  purgeDatabase,
  refreshDescriptions,
  refreshFaces,
  refreshLocations,
  untagFace,
  updateIndividual,
  updateTag,
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
  // recognition.py's background face-matching job -- null means idle/no
  // job ever started this page visit. Polling mirrors App.jsx's
  // pollSyncStatus/SyncStatus pattern, scoped locally to this page instead
  // of global app state, since it's specific to the individuals catalog.
  // Both the non-destructive "Refresh Faces" button and the destructive
  // "Full Face Refresh" button (down in the danger zone -- see
  // routers/faces.py's `full` query param) share this single poll
  // loop/state, since the backend only ever runs one such job at a time
  // regardless of which button started it (recognition.py's
  // `_RefreshProgress.try_start()`). `refreshStatus.full` -- set by the
  // backend, not tracked separately here -- is what each button's render
  // branch below checks to tell "my job" apart from "the other button's
  // job" when deciding what to show.
  const [refreshStatus, setRefreshStatus] = useState(null);
  const refreshPollRef = useRef(null);
  // "Refresh Descriptions" (enrich.py's bulk enrich_description job) --
  // same shape as refreshStatus/refreshPollRef above, but its own state
  // since the two jobs are independent and can run/poll concurrently.
  const [descriptionRefreshStatus, setDescriptionRefreshStatus] = useState(null);
  const descriptionRefreshPollRef = useRef(null);
  // "Refresh Locations" (geocode.py's bulk re-geocode job) -- same shape
  // again, its own independent state/poll loop.
  const [locationRefreshStatus, setLocationRefreshStatus] = useState(null);
  const locationRefreshPollRef = useRef(null);
  const [purgeError, setPurgeError] = useState(null);

  // "Identify Duplicates" -- a synchronous CSV download (storage.
  // find_duplicate_photos via GET /api/photos/duplicates/csv), not a
  // background job like the three refreshes above, so there's no status
  // to poll: just an in-flight flag (to disable the button/show progress
  // text while the request is out) and an error slot.
  const [duplicatesLoading, setDuplicatesLoading] = useState(false);
  const [duplicatesError, setDuplicatesError] = useState(null);

  // Tag catalog (tags.py/routers/tags.py) -- a separate full-CRUD list
  // from the individuals catalog above, shown with its photo count only
  // (never the tagged photos themselves, unlike the per-individual
  // accordion). Same add/rename state shape as individuals.
  const [tags, setTags] = useState(null);
  const [tagsError, setTagsError] = useState(null);
  const [newTagName, setNewTagName] = useState("");
  const [addTagError, setAddTagError] = useState(null);
  const [renamingTagId, setRenamingTagId] = useState(null);
  const [renameTagDraft, setRenameTagDraft] = useState("");

  useEffect(() => {
    fetchIndividuals()
      .then(setIndividuals)
      .catch((err) => setPageError(err.message));
    fetchTags()
      .then(setTags)
      .catch((err) => setTagsError(err.message));
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
    // Same "resume polling if already running" check, for a location
    // refresh left running from before this page was (re)opened.
    fetchLocationRefreshStatus()
      .then((status) => {
        if (status.status === "running") {
          setLocationRefreshStatus(status);
          pollLocationRefreshStatus();
        }
      })
      .catch(() => {});
    return () => {
      clearInterval(refreshPollRef.current);
      clearInterval(descriptionRefreshPollRef.current);
      clearInterval(locationRefreshPollRef.current);
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

  function pollLocationRefreshStatus() {
    clearInterval(locationRefreshPollRef.current);
    locationRefreshPollRef.current = setInterval(async () => {
      try {
        const status = await fetchLocationRefreshStatus();
        if (status.status === "running") {
          setLocationRefreshStatus(status);
          return;
        }
        clearInterval(locationRefreshPollRef.current);
        setLocationRefreshStatus(status);
        // Like description refresh, the result is only visible on a
        // photo's own modal/map -- no reload needed here.
        setTimeout(() => setLocationRefreshStatus(null), REFRESH_DONE_DISPLAY_MS);
      } catch {
        clearInterval(locationRefreshPollRef.current);
        setLocationRefreshStatus(null);
      }
    }, REFRESH_POLL_INTERVAL_MS);
  }

  function handleRefreshLocations() {
    refreshLocations()
      .then((status) => {
        setLocationRefreshStatus(status);
        pollLocationRefreshStatus();
      })
      .catch((err) => setPageError(err.message));
  }

  // Non-destructive: matches still-untagged faces against people already
  // tagged at least once, leaving every existing box/tag completely
  // alone. Same scope sync.py's own post-sync trigger always uses
  // (routers/faces.py's `full` query param defaults to false) -- safe to
  // run any time, no confirmation needed.
  function handleRefreshFaceMatches() {
    refreshFaces(false)
      .then((status) => {
        setRefreshStatus(status);
        pollRefreshStatus();
      })
      .catch((err) => setPageError(err.message));
  }

  // Re-detects every photo's faces against the current detection settings
  // and discards every existing face tag, manual or auto, along with the
  // stale boxes they were on (see recognition.py's module docstring) --
  // same "confirm the one destructive, irreversible, global action" shape
  // as handlePurge below, just scoped to faces/tags rather than the whole
  // database. Lives in the danger zone for the same reason.
  function handleFullFaceRefresh() {
    if (
      !window.confirm(
        "This re-detects faces in every photo and deletes all existing face tags -- manual and " +
          "automatic -- along with them. You'll need to re-tag individuals afterward before " +
          "auto-matching can find anything. This cannot be undone. Continue?"
      )
    ) {
      return;
    }
    refreshFaces(true)
      .then((status) => {
        setRefreshStatus(status);
        pollRefreshStatus();
      })
      .catch((err) => setPageError(err.message));
  }

  function handleIdentifyDuplicates() {
    setDuplicatesLoading(true);
    setDuplicatesError(null);
    downloadDuplicatesCsv()
      .catch((err) => setDuplicatesError(err.message))
      .finally(() => setDuplicatesLoading(false));
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

  function handleAddTag(event) {
    event.preventDefault();
    const name = newTagName.trim();
    if (!name) return;
    createTag(name)
      .then((created) => {
        setTags((prev) => sortByName([...(prev ?? []), created]));
        setNewTagName("");
        setAddTagError(null);
      })
      .catch((err) => setAddTagError(err.message));
  }

  function startRenameTag(tag) {
    setRenamingTagId(tag.id);
    setRenameTagDraft(tag.name);
  }

  function cancelRenameTag() {
    setRenamingTagId(null);
    setRenameTagDraft("");
  }

  function saveRenameTag(tagId) {
    const name = renameTagDraft.trim();
    if (!name) return;
    updateTag(tagId, name)
      .then((updated) => {
        setTags((prev) => sortByName(prev.map((tag) => (tag.id === tagId ? updated : tag))));
        setTagsError(null);
        cancelRenameTag();
      })
      .catch((err) => setTagsError(err.message));
  }

  function handleListTag(tag) {
    downloadTagCsv(tag)
      .then(() => setTagsError(null))
      .catch((err) => setTagsError(err.message));
  }

  function handleDeleteTag(tagId) {
    deleteTag(tagId)
      .then(() => {
        setTags((prev) => prev.filter((tag) => tag.id !== tagId));
        setTagsError(null);
      })
      .catch((err) => setTagsError(err.message));
  }

  // Purges the whole database (photos, faces, individuals, tags) --
  // irreversible and global, unlike the per-item Delete buttons above, so
  // it's the one destructive action here that confirms first.
  function handlePurge() {
    if (
      !window.confirm(
        "This permanently deletes every photo, face, individual, and tag from the database. " +
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
        setTags([]);
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

      <h2>Refresh metadata</h2>

      <div className="management-refresh-zone">
        <div className="management-refresh-row">
          <div className="management-refresh-control">
            <SyncStatus status={syncStatus} onRefresh={onRefresh} />
          </div>
          <p className="management-refresh-description">
            Scans the photos backend for new, changed, or removed files and updates the index to
            match. Run this after adding or removing photos outside Luminary.
          </p>
        </div>

        <div className="management-refresh-row">
          <div className="management-refresh-control">
            {refreshStatus && refreshStatus.full && refreshStatus.status === "running" ? (
              <div className="sync-status" role="status" aria-live="polite">
                A full face refresh is running…
              </div>
            ) : !refreshStatus || refreshStatus.status === "idle" || refreshStatus.full ? (
              <button type="button" className="refresh-button" onClick={handleRefreshFaceMatches}>
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
                        refreshStatus.total > 0
                          ? Math.round((refreshStatus.processed / refreshStatus.total) * 100)
                          : 0
                      }%`}
                </span>
                <div className="sync-progress-track">
                  <div
                    className="sync-progress-fill"
                    style={{
                      width: `${
                        refreshStatus.total > 0
                          ? Math.round((refreshStatus.processed / refreshStatus.total) * 100)
                          : 0
                      }%`,
                    }}
                  />
                </div>
              </div>
            )}
          </div>
          <p className="management-refresh-description">
            Matches still-untagged faces against people you've already tagged at least once, and
            auto-assigns the confident matches. Leaves existing face detections and tags untouched.
          </p>
        </div>

        <div className="management-refresh-row">
          <div className="management-refresh-control">
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
                          ? Math.round(
                              (descriptionRefreshStatus.processed / descriptionRefreshStatus.total) * 100
                            )
                          : 0
                      }%`}
                </span>
                <div className="sync-progress-track">
                  <div
                    className="sync-progress-fill"
                    style={{
                      width: `${
                        descriptionRefreshStatus.total > 0
                          ? Math.round(
                              (descriptionRefreshStatus.processed / descriptionRefreshStatus.total) * 100
                            )
                          : 0
                      }%`,
                    }}
                  />
                </div>
              </div>
            )}
          </div>
          <p className="management-refresh-description">
            Rewrites each photo's description from its caption, tagged people, and location --
            useful after tagging or renaming people.
          </p>
        </div>

        <div className="management-refresh-row">
          <div className="management-refresh-control">
            {!locationRefreshStatus || locationRefreshStatus.status === "idle" ? (
              <button type="button" className="refresh-button" onClick={handleRefreshLocations}>
                Refresh Locations
              </button>
            ) : locationRefreshStatus.status === "error" ? (
              <div className="sync-status sync-status-error" role="status">
                Refresh failed{locationRefreshStatus.error ? `: ${locationRefreshStatus.error}` : ""}
              </div>
            ) : (
              <div className="sync-status" role="status" aria-live="polite">
                <span className="sync-status-label">
                  {locationRefreshStatus.status === "completed"
                    ? `Done -- ${locationRefreshStatus.updated} updated`
                    : `Refreshing locations… ${
                        locationRefreshStatus.total > 0
                          ? Math.round((locationRefreshStatus.processed / locationRefreshStatus.total) * 100)
                          : 0
                      }%`}
                </span>
                <div className="sync-progress-track">
                  <div
                    className="sync-progress-fill"
                    style={{
                      width: `${
                        locationRefreshStatus.total > 0
                          ? Math.round((locationRefreshStatus.processed / locationRefreshStatus.total) * 100)
                          : 0
                      }%`,
                    }}
                  />
                </div>
              </div>
            )}
          </div>
          <p className="management-refresh-description">
            Re-geocodes every photo that already has GPS coordinates -- useful after changing the
            geocoding zoom level, or to pick up a named business/street address for photos indexed
            before that detail was added.
          </p>
        </div>

        <div className="management-refresh-row">
          <div className="management-refresh-control">
            <button
              type="button"
              className="refresh-button"
              onClick={handleIdentifyDuplicates}
              disabled={duplicatesLoading}
            >
              {duplicatesLoading ? "Preparing CSV…" : "Identify Duplicates"}
            </button>
          </div>
          <p className="management-refresh-description">
            Downloads a CSV of byte-identical photos, found by comparing checksums -- one row per
            duplicate file, with the original it duplicates in the first column. Run the sync above
            first if any photos have not been checksummed yet.
          </p>
        </div>
      </div>

      {duplicatesError && (
        <p role="alert" className="error">
          {duplicatesError}
        </p>
      )}

      {pageError && (
        <p role="alert" className="error">
          {pageError}
        </p>
      )}

      <h2>Manage individuals</h2>

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

      <h2>Manage tags</h2>

      {tagsError && (
        <p role="alert" className="error">
          {tagsError}
        </p>
      )}

      <form className="management-add-tag" onSubmit={handleAddTag}>
        <input
          type="text"
          value={newTagName}
          onChange={(event) => setNewTagName(event.target.value)}
          placeholder="Add a tag…"
        />
        <button type="submit" disabled={!newTagName.trim()}>
          Add
        </button>
      </form>
      {addTagError && (
        <p role="alert" className="error">
          {addTagError}
        </p>
      )}

      {tags === null ? (
        <p>Loading…</p>
      ) : tags.length === 0 ? (
        <p className="empty-state">No tags catalogued yet.</p>
      ) : (
        <ul className="management-tags">
          {tags.map((tag) => {
            const renaming = renamingTagId === tag.id;
            return (
              <li key={tag.id} className="management-tag">
                {renaming ? (
                  <input
                    className="management-rename-input"
                    value={renameTagDraft}
                    autoFocus
                    onChange={(event) => setRenameTagDraft(event.target.value)}
                  />
                ) : (
                  <span className="management-tag-name">{tag.name}</span>
                )}

                <span className="management-tag-count">
                  {tag.photo_count} photo{tag.photo_count === 1 ? "" : "s"}
                </span>

                <span className="management-tag-actions">
                  {renaming ? (
                    <>
                      <button type="button" onClick={() => saveRenameTag(tag.id)} disabled={!renameTagDraft.trim()}>
                        Save
                      </button>
                      <button type="button" onClick={cancelRenameTag}>
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <button type="button" onClick={() => handleListTag(tag)}>
                        List
                      </button>
                      <button type="button" onClick={() => startRenameTag(tag)}>
                        Rename
                      </button>
                      <button type="button" className="management-delete" onClick={() => handleDeleteTag(tag.id)}>
                        Delete
                      </button>
                    </>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}

      <div className="management-danger-zone">
        <h3>Danger zone</h3>

        <div className="management-refresh-row">
          <div className="management-refresh-control">
            {refreshStatus && !refreshStatus.full && refreshStatus.status === "running" ? (
              <div className="sync-status" role="status" aria-live="polite">
                "Refresh Faces" is running…
              </div>
            ) : !refreshStatus || refreshStatus.status === "idle" || !refreshStatus.full ? (
              <button type="button" className="management-purge" onClick={handleFullFaceRefresh}>
                Full Face Refresh
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
                    : `Redetecting & matching faces… ${
                        refreshStatus.total > 0
                          ? Math.round((refreshStatus.processed / refreshStatus.total) * 100)
                          : 0
                      }%`}
                </span>
                <div className="sync-progress-track">
                  <div
                    className="sync-progress-fill"
                    style={{
                      width: `${
                        refreshStatus.total > 0
                          ? Math.round((refreshStatus.processed / refreshStatus.total) * 100)
                          : 0
                      }%`,
                    }}
                  />
                </div>
              </div>
            )}
          </div>
          <p className="management-refresh-description">
            Re-detects faces in every photo against the current detection settings, and deletes all
            existing face tags -- manual and automatic -- along with them. You'll need to re-tag
            individuals afterward before auto-matching can find anything. This cannot be undone.
          </p>
        </div>

        <p>
          Permanently deletes every photo, face, individual, and tag from the database. The
          original files on the photos backend are untouched and will be re-indexed on the next
          sync.
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
