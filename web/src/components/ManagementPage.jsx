import { useEffect, useRef, useState } from "react";
import {
  cancelJob,
  createIndividual,
  createTag,
  deleteIndividual,
  deleteRejection,
  deleteTag,
  downloadDuplicatesCsv,
  downloadTagCsv,
  faceThumbnailUrl,
  fetchIndividualFaces,
  fetchIndividualRejections,
  fetchIndividuals,
  fetchTags,
  individualThumbnailUrl,
  originalUrl,
  purgeDatabase,
  refreshDescriptions,
  refreshFaces,
  refreshLocations,
  rejectionThumbnailUrl,
  untagFace,
  updateIndividual,
  updateTag,
} from "../api/client.js";
import JobControl from "./JobControl.jsx";

const REFRESH_DONE_DISPLAY_MS = 2000;
const SOURCE_PREVIEW_DELAY_MS = 5000;

export default function ManagementPage({ jobs, onJobsChange, onRefresh, onBack, onNavigateToPhoto, onPurged }) {
  const [individuals, setIndividuals] = useState(null);
  // Photo a hovered auto-matched face came from, shown in a popup after the
  // pointer rests on the face for SOURCE_PREVIEW_DELAY_MS.
  const [previewPhotoId, setPreviewPhotoId] = useState(null);
  const previewTimer = useRef(null);

  function startPreviewTimer(photoId) {
    clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => setPreviewPhotoId(photoId), SOURCE_PREVIEW_DELAY_MS);
  }

  function cancelPreviewTimer() {
    clearTimeout(previewTimer.current);
  }

  useEffect(() => () => clearTimeout(previewTimer.current), []);

  useEffect(() => {
    if (previewPhotoId === null) return undefined;
    const close = () => setPreviewPhotoId(null);
    const onKey = (event) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", close);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", close);
    };
  }, [previewPhotoId]);
  const [pageError, setPageError] = useState(null);
  const [newName, setNewName] = useState("");
  const [addError, setAddError] = useState(null);
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  // Keyed by individual id; a key's absence means "not fetched yet" for
  // that individual (distinct from an empty array, "fetched, no tagged
  // faces") -- fetched lazily, the first time that individual's accordion
  // is expanded, same pattern as PhotoModal.jsx's faceBoxes/individuals.
  const [facesByIndividual, setFacesByIndividual] = useState({});
  // Same convention, for faces the user untagged after they were
  // auto-matched (face_rejections) -- shown in a collapsed section under
  // each individual's faces.
  const [rejectionsByIndividual, setRejectionsByIndividual] = useState({});
  const [renamingId, setRenamingId] = useState(null);
  const [renameDraft, setRenameDraft] = useState("");
  // `jobs` is the server's one shared snapshot of every background job
  // (App.jsx polls GET /api/jobs while this page is open), so a job started
  // from any browser shows up here, and only one can run at a time. The
  // kind that most recently finished is remembered briefly here so its
  // "Done" text can show for a moment, then go away -- an error stays up
  // (it lives in the server's snapshot until the next run replaces it).
  const [justFinished, setJustFinished] = useState(null);
  const previousActiveRef = useRef(null);
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
  }, []);

  // Notice a job ending, whichever browser started it. Face matching
  // (a sync auto-triggers it too) may have tagged faces, so the catalog
  // reloads; any ended job shows its outcome for a moment.
  useEffect(() => {
    const previous = previousActiveRef.current;
    const current = jobs?.active ?? null;
    previousActiveRef.current = current;
    if (!previous || previous === current || !jobs) return undefined;
    if (previous === "faces" && jobs.faces?.status === "completed") reloadAfterRefresh();
    setJustFinished(previous);
    // No cleanup: `jobs` is a fresh object every poll, which would cancel
    // this timer before it fired.
    setTimeout(() => setJustFinished(null), REFRESH_DONE_DISPLAY_MS);
    return undefined;
  }, [jobs]);

  function sortByName(list) {
    return [...list].sort((a, b) => a.name.localeCompare(b.name));
  }

  function loadRejections(personId) {
    fetchIndividualRejections(personId)
      .then((rejections) => setRejectionsByIndividual((prev) => ({ ...prev, [personId]: rejections })))
      .catch((err) => setPageError(err.message));
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
        if (!(personId in rejectionsByIndividual)) {
          loadRejections(personId);
        }
      }
      return next;
    });
  }

  // Re-fetches anything face matching may have changed: the catalog
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
    Object.keys(rejectionsByIndividual).forEach(loadRejections);
  }

  async function handleCancel() {
    try {
      onJobsChange?.(await cancelJob());
    } catch (err) {
      setPageError(err.message);
    }
  }

  function startJob(start) {
    start()
      .then(() => onJobsChange?.())
      .catch((err) => {
        setPageError(err.message);
        onJobsChange?.();
      });
  }

  function handleRefreshDescriptions() {
    startJob(refreshDescriptions);
  }

  function handleRefreshLocations() {
    startJob(refreshLocations);
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
    startJob(() => refreshFaces(true));
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
        // Untagging an auto-matched face records a rejection.
        loadRejections(personId);
      })
      .catch((err) => setPageError(err.message));
  }

  function handleAllowRematch(personId, rejectionId) {
    deleteRejection(rejectionId)
      .then(() => {
        setRejectionsByIndividual((prev) => ({
          ...prev,
          [personId]: (prev[personId] ?? []).filter((rejection) => rejection.id !== rejectionId),
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
            <JobControl
              kind="sync"
              jobs={jobs}
              justFinished={justFinished}
              idleLabel="Refresh"
              running={(_state, pct) => `Syncing ${pct}%`}
              done={() => "Done"}
              failed={() => "Sync failed"}
              onStart={onRefresh}
              onCancel={handleCancel}
            />
            {/* The face matching a sync triggers (not the danger-zone full refresh). */}
            <JobControl
              kind="faces"
              jobs={jobs}
              justFinished={justFinished}
              owns={(state) => !state.full}
              running={(_state, pct) => `Matching faces… ${pct}%`}
              done={(state) => `Faces done -- ${state.matched} matched`}
              failed={(state) => `Face matching failed${state.error ? `: ${state.error}` : ""}`}
              onCancel={handleCancel}
            />
          </div>
          <p className="management-refresh-description">
            Scans the photos backend for new, changed, or removed files and updates the index to
            match. Run this after adding or removing photos outside Luminary.
          </p>
        </div>

        <div className="management-refresh-row">
          <div className="management-refresh-control">
            <JobControl
              kind="descriptions"
              jobs={jobs}
              justFinished={justFinished}
              idleLabel="Refresh Descriptions"
              running={(_state, pct) => `Refreshing descriptions… ${pct}%`}
              done={(state) => `Done -- ${state.updated} updated`}
              failed={(state) => `Refresh failed${state.error ? `: ${state.error}` : ""}`}
              onStart={handleRefreshDescriptions}
              onCancel={handleCancel}
            />
          </div>
          <p className="management-refresh-description">
            Rewrites each photo&apos;s description from its caption, tagged people, and location --
            useful after tagging or renaming people.
          </p>
        </div>

        <div className="management-refresh-row">
          <div className="management-refresh-control">
            <JobControl
              kind="locations"
              jobs={jobs}
              justFinished={justFinished}
              idleLabel="Refresh Locations"
              running={(_state, pct) => `Refreshing locations… ${pct}%`}
              done={(state) => `Done -- ${state.updated} updated`}
              failed={(state) => `Refresh failed${state.error ? `: ${state.error}` : ""}`}
              onStart={handleRefreshLocations}
              onCancel={handleCancel}
            />
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
                        <div
                          key={face.id}
                          className="management-face"
                          onMouseEnter={() => startPreviewTimer(face.photo_id)}
                          onMouseLeave={cancelPreviewTimer}
                        >
                          <button
                            type="button"
                            className="management-face-image"
                            onClick={() => onNavigateToPhoto(face.photo_id)}
                            title="Go to the photo this face was tagged from"
                          >
                            <img src={faceThumbnailUrl(face.id)} alt="" />
                          </button>
                          {face.tag_source === "auto" && (
                            <span className="management-face-auto-badge">
                              auto
                              {typeof face.match_confidence === "number" &&
                                ` - ${Math.round(face.match_confidence * 100)}%`}
                            </span>
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
                    {(rejectionsByIndividual[individual.id] ?? []).length > 0 && (
                      <details className="management-rejections">
                        <summary>Unmatched auto-tags ({rejectionsByIndividual[individual.id].length})</summary>
                        <p className="management-rejections-hint">
                          Faces you removed after they were auto-matched to {individual.name}. They won&apos;t be
                          matched again unless you allow it.
                        </p>
                        <div className="management-rejections-faces">
                          {rejectionsByIndividual[individual.id].map((rejection) => (
                            <div
                              key={rejection.id}
                              className="management-face"
                              onMouseEnter={() => startPreviewTimer(rejection.photo_id)}
                              onMouseLeave={cancelPreviewTimer}
                            >
                              <button
                                type="button"
                                className="management-face-image"
                                onClick={() => onNavigateToPhoto(rejection.photo_id)}
                                title="Go to the photo this face is in"
                              >
                                <img src={rejectionThumbnailUrl(rejection.id)} alt="" />
                              </button>
                              <button
                                type="button"
                                className="management-face-remove"
                                onClick={() => handleAllowRematch(individual.id, rejection.id)}
                                aria-label="Allow matching again"
                                title="Allow matching again"
                              >
                                ×
                              </button>
                            </div>
                          ))}
                        </div>
                      </details>
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
            <JobControl
              kind="faces"
              jobs={jobs}
              justFinished={justFinished}
              owns={(state) => Boolean(state.full)}
              idleLabel="Full Face Refresh"
              buttonClass="management-purge"
              running={(_state, pct) => `Redetecting & matching faces… ${pct}%`}
              done={(state) => `Done -- ${state.matched} matched`}
              failed={(state) => `Refresh failed${state.error ? `: ${state.error}` : ""}`}
              onStart={handleFullFaceRefresh}
              onCancel={handleCancel}
            />
          </div>
          <p className="management-refresh-description">
            Re-detects faces in every photo against the current detection settings, and deletes all
            existing face tags -- manual and automatic -- along with them. You&apos;ll need to re-tag
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
      {previewPhotoId !== null && (
        <div className="management-source-preview" role="dialog" aria-label="Source photo">
          <img src={originalUrl(previewPhotoId)} alt="Source photo for this face" />
        </div>
      )}
    </div>
  );
}
