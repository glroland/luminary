import { useEffect, useMemo, useRef, useState } from "react";
import { fetchPhotoTags, fetchTags, tagPhoto, untagPhoto } from "../api/client.js";
import PhotoCard from "./PhotoCard.jsx";

// How many more photos each scroll-to-the-bottom reveals (and how many
// are rendered to begin with).
export const BATCH_SIZE = 60;

// "month" groups by the calendar month of created_at, "folder" by the
// photo's directory (everything in filename before the last "/").
function groupKeyOf(photo, groupBy) {
  if (groupBy === "folder") {
    const slash = photo.filename.lastIndexOf("/");
    return slash === -1 ? "" : photo.filename.slice(0, slash);
  }
  const date = new Date(photo.created_at);
  if (!photo.created_at || Number.isNaN(date.getTime())) return "undated";
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function groupLabelOf(photo, groupBy) {
  if (groupBy === "folder") return groupKeyOf(photo, groupBy) || "(Root)";
  const date = new Date(photo.created_at);
  if (!photo.created_at || Number.isNaN(date.getTime())) return "Undated";
  return date.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

// `photos` is the *whole* list to browse (already filtered and sorted by
// App.jsx), not one page of it: this component renders the first
// BATCH_SIZE and reveals another batch each time the sentinel at the
// bottom scrolls into view. That window is plain local state, so App.jsx
// remounts this component (via `key`) whenever the filter/sort changes to
// start again from the top.
//
// `groupBy` ("month" | "folder" | null) splits the grid into sections with
// a sticky header each. Sections are runs of *consecutive* photos sharing
// a key, so it only makes sense when `photos` is sorted by that same
// thing -- App.jsx picks it from the sort order, and passes null for
// filename sorts and search results (ordered by relevance).
export default function PhotoGrid({ photos, onSelect, emptyMessage = "No photos yet.", groupBy = null }) {
  const [visibleCount, setVisibleCount] = useState(BATCH_SIZE);
  const sentinelRef = useRef(null);
  // Without IntersectionObserver there's no way to notice the bottom being
  // reached, so everything is rendered up front rather than stranding the
  // rest of the list out of reach.
  const canObserve = typeof IntersectionObserver !== "undefined";
  const shownCount = canObserve ? Math.min(visibleCount, photos.length) : photos.length;
  const hasMore = shownCount < photos.length;

  // Re-created whenever a batch is added (shownCount in the deps), not
  // just once: a new observer reports its target's current state straight
  // away, so if the sentinel is *still* in view after a batch renders (a
  // tall window, a short batch) the next one loads too, instead of waiting
  // for an intersection change that would never come.
  useEffect(() => {
    if (!hasMore || !sentinelRef.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisibleCount((prev) => prev + BATCH_SIZE);
        }
      },
      // Start loading well before the bottom is actually on screen.
      { rootMargin: "800px 0px" }
    );
    observer.observe(sentinelRef.current);
    return () => observer.disconnect();
  }, [hasMore, shownCount]);

  // Section sizes come from the full list, so a header's count is the
  // real total for that month/folder even while only part of it has been
  // scrolled into existence.
  const sections = useMemo(() => {
    if (!groupBy) return null;
    const result = [];
    photos.forEach((photo, index) => {
      const key = groupKeyOf(photo, groupBy);
      const last = result[result.length - 1];
      if (last && last.key === key) {
        last.end = index + 1;
      } else {
        result.push({ key, label: groupLabelOf(photo, groupBy), start: index, end: index + 1 });
      }
    });
    return result;
  }, [photos, groupBy]);

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

  function renderCards(start, end) {
    return photos.slice(start, Math.min(end, shownCount)).map((photo) => (
      <PhotoCard
        key={photo.id}
        photo={photo}
        onSelect={onSelect}
        onContextMenu={(event) => handleContextMenu(event, photo)}
      />
    ));
  }

  return (
    <div className="photo-gallery">
      {sections ? (
        sections
          .filter((section) => section.start < shownCount)
          .map((section) => (
            <section key={`${section.key}@${section.start}`}>
              <h2 className="photo-section-header">
                {section.label}
                <span className="photo-section-count">{(section.end - section.start).toLocaleString()}</span>
              </h2>
              <div className="photo-grid">{renderCards(section.start, section.end)}</div>
            </section>
          ))
      ) : (
        <div className="photo-grid">{renderCards(0, photos.length)}</div>
      )}
      {hasMore && <div ref={sentinelRef} className="photo-grid-sentinel" aria-hidden="true" />}

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
