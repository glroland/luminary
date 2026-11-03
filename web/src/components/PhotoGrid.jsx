import { useEffect, useState } from "react";
import { fetchPhotoTags, fetchTags, tagPhoto, untagPhoto } from "../api/client.js";
import PhotoCard from "./PhotoCard.jsx";

export default function PhotoGrid({ photos, onSelect, emptyMessage = "No photos yet." }) {
  // Right-click-to-tag menu (mirrors PhotoModal.jsx's face tag menu, but a
  // checklist rather than a single-pick-and-close menu: multiple tags can
  // be toggled on/off in one right-click, since a photo can carry several
  // at once). {photoId, x, y} while open, else null. x/y are viewport
  // coordinates (the contextmenu event's clientX/clientY) since the menu
  // is positioned with `position: fixed`.
  const [tagMenu, setTagMenu] = useState(null);
  // The full tag catalog, fetched lazily on the first right-click rather
  // than eagerly on mount -- most gallery views never open this menu, so
  // there's no reason to fetch it before it's needed. null means "not
  // fetched yet".
  const [catalogTags, setCatalogTags] = useState(null);
  // Keyed by photo id; a key's absence means "not fetched yet" for that
  // photo (distinct from an empty array, "fetched, no tags") -- fetched
  // lazily the first time that photo's menu is opened, same pattern as
  // ManagementPage.jsx's facesByIndividual.
  const [tagsByPhoto, setTagsByPhoto] = useState({});
  const [tagActionError, setTagActionError] = useState(null);

  useEffect(() => {
    if (!tagMenu) return;

    function handleWindowClick() {
      setTagMenu(null);
    }
    function handleKeyDown(event) {
      if (event.key === "Escape") setTagMenu(null);
    }

    window.addEventListener("click", handleWindowClick);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("click", handleWindowClick);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [tagMenu]);

  function handleContextMenu(event, photo) {
    event.preventDefault();
    setTagMenu({ photoId: photo.id, x: event.clientX, y: event.clientY });
    setTagActionError(null);
    if (catalogTags === null) {
      fetchTags()
        .then(setCatalogTags)
        .catch((err) => setTagActionError(err.message));
    }
    if (!(photo.id in tagsByPhoto)) {
      fetchPhotoTags(photo.id)
        .then((tags) => setTagsByPhoto((prev) => ({ ...prev, [photo.id]: tags })))
        .catch((err) => setTagActionError(err.message));
    }
  }

  function handleToggleTag(photoId, tag) {
    const current = tagsByPhoto[photoId] ?? [];
    const applied = current.some((candidate) => candidate.id === tag.id);
    const action = applied ? untagPhoto(photoId, tag.id) : tagPhoto(photoId, tag.id);
    action
      .then(() => {
        setTagsByPhoto((prev) => {
          const existing = prev[photoId] ?? [];
          const next = applied
            ? existing.filter((candidate) => candidate.id !== tag.id)
            : [...existing, tag].sort((a, b) => a.name.localeCompare(b.name));
          return { ...prev, [photoId]: next };
        });
        setTagActionError(null);
      })
      .catch((err) => setTagActionError(err.message));
  }

  if (photos.length === 0) {
    return <p className="empty-state">{emptyMessage}</p>;
  }

  const photoTagsLoaded = tagMenu && tagMenu.photoId in tagsByPhoto;

  return (
    <div className="photo-grid">
      {photos.map((photo) => (
        <PhotoCard
          key={photo.id}
          photo={photo}
          onSelect={onSelect}
          onContextMenu={(event) => handleContextMenu(event, photo)}
        />
      ))}

      {tagMenu && (
        <div className="tag-menu" style={{ left: tagMenu.x, top: tagMenu.y }} onClick={(event) => event.stopPropagation()}>
          {catalogTags === null || !photoTagsLoaded ? (
            <div className="tag-menu-item tag-menu-message">Loading…</div>
          ) : catalogTags.length === 0 ? (
            <div className="tag-menu-item tag-menu-message">No tags catalogued yet</div>
          ) : (
            catalogTags.map((tag) => {
              const applied = (tagsByPhoto[tagMenu.photoId] ?? []).some((candidate) => candidate.id === tag.id);
              return (
                <div
                  key={tag.id}
                  className={`tag-menu-item${applied ? " tag-menu-item-checked" : ""}`}
                  onClick={() => handleToggleTag(tagMenu.photoId, tag)}
                >
                  <span className="tag-menu-item-check">{applied ? "✓" : ""}</span>
                  {tag.name}
                </div>
              );
            })
          )}
          {tagActionError && <div className="tag-menu-item tag-menu-error">{tagActionError}</div>}
        </div>
      )}
    </div>
  );
}
