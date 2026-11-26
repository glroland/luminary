import { useEffect, useMemo, useRef, useState } from "react";
import { fetchPhotoTags, fetchTags, tagPhoto, untagPhoto } from "../api/client.js";
import PhotoCard from "./PhotoCard.jsx";

// How many more photos each scroll-to-the-bottom reveals (and how many
// are rendered to begin with).
export const BATCH_SIZE = 60;

// How many tag requests a bulk tag/untag keeps in flight at once.
const TAG_CONCURRENCY = 8;

// Run `fn` over `items` with at most `limit` in flight; never rejects --
// resolves to one {ok, error} per item, in order.
async function mapLimited(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const index = next++;
      try {
        await fn(items[index]);
        results[index] = { ok: true };
      } catch (error) {
        results[index] = { ok: false, error };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

// The calendar day written in a created_at string, as-is. An EXIF time is the
// wall clock where the photo was taken (no real zone), so a trip's days are
// read off the string rather than converted through the browser's timezone.
function wallClockDay(createdAt) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(createdAt ?? "");
  if (!match) return null;
  return {
    key: `${match[1]}-${match[2]}-${match[3]}`,
    date: new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])),
  };
}

// "month" groups by the calendar month of created_at, "day" by its day (a
// trip's gallery), "folder" by the
// photo's directory (everything in filename before the last "/").
function groupKeyOf(photo, groupBy) {
  if (groupBy === "folder") {
    const slash = photo.filename.lastIndexOf("/");
    return slash === -1 ? "" : photo.filename.slice(0, slash);
  }
  if (groupBy === "day") return wallClockDay(photo.created_at)?.key ?? "undated";
  const date = new Date(photo.created_at);
  if (!photo.created_at || Number.isNaN(date.getTime())) return "undated";
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function groupLabelOf(photo, groupBy) {
  if (groupBy === "folder") return groupKeyOf(photo, groupBy) || "(Root)";
  if (groupBy === "day") {
    const day = wallClockDay(photo.created_at);
    return day
      ? day.date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" })
      : "Undated";
  }
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
// `groupBy` ("month" | "day" | "folder" | null) splits the grid into sections with
// a sticky header each. Sections are runs of *consecutive* photos sharing
// a key, so it only makes sense when `photos` is sorted by that same
// thing -- App.jsx picks it from the sort order, and passes null for
// filename sorts and search results (ordered by relevance).
//
// `onToggleSidebar`/`sidebarOpen` (optional) put a copy of the header's
// hamburger button in whichever section header is currently stuck to the
// top of the viewport, so the sidebar stays reachable once the page
// header itself has scrolled away.
export default function PhotoGrid({
  photos,
  onSelect,
  emptyMessage = "No photos yet.",
  groupBy = null,
  onToggleSidebar = null,
  sidebarOpen = false,
  keysEnabled = true,
  // Trips (all optional). `trips` is every trip a photo can be added to;
  // `currentTripId` is the trip being browsed, if any. Each handler returns
  // a promise: a rejection's message is shown in the menu.
  trips = [],
  currentTripId = null,
  onAddToTrip = null,
  onRemoveFromTrip = null,
  onSetTripCover = null,
}) {
  const [visibleCount, setVisibleCount] = useState(BATCH_SIZE);
  const galleryRef = useRef(null);
  // The `data-section-id` of the section whose header is stuck to the top
  // of the viewport right now, or null when none is (scrolled to the top,
  // or an ungrouped grid).
  const [stuckSectionId, setStuckSectionId] = useState(null);
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

  // Tracks which section's header is stuck. A section straddling the top
  // edge of the viewport is the one whose sticky header is pinned there --
  // and since the page header sits above the whole gallery, that also
  // means the page header (and its own sidebar button) is out of view.
  // State only changes when the stuck section does, so scrolling within a
  // section re-renders nothing.
  useEffect(() => {
    if (!sections || !onToggleSidebar) return;
    let frame = null;
    function measure() {
      frame = null;
      let stuck = null;
      for (const element of galleryRef.current?.querySelectorAll("section[data-section-id]") ?? []) {
        const rect = element.getBoundingClientRect();
        if (rect.top <= 1 && rect.bottom > 1) {
          stuck = element.dataset.sectionId;
          break;
        }
      }
      setStuckSectionId(stuck);
    }
    function schedule() {
      if (frame === null) frame = requestAnimationFrame(measure);
    }
    measure();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [sections, shownCount, onToggleSidebar]);

  // Multi-selection, by photo id. `anchor` is where a shift-range starts
  // and `focus` is the end the arrow keys move; both are null when nothing
  // is selected. Ctrl/Cmd-click toggles one photo, Shift-click or
  // Shift+arrow selects the range anchor..target, a plain arrow moves a
  // single selection, Escape clears it.
  const [selection, setSelection] = useState({ ids: new Set(), anchor: null, focus: null });
  const selectionRef = useRef(selection);
  selectionRef.current = selection;

  // Right-click-to-tag menu (mirrors PhotoModal.jsx's face tag menu, but a
  // checklist rather than a single-pick-and-close menu: multiple tags can
  // be toggled on/off in one right-click, since a photo can carry several
  // at once). {photoIds, x, y} while open, else null. It acts on every
  // photo in `photoIds`: the whole selection when the right-clicked photo
  // is part of one, otherwise just that photo. x/y are viewport
  // coordinates (the contextmenu event's clientX/clientY) since the menu
  // is positioned with `position: fixed`.
  const [tagMenu, setTagMenu] = useState(null);
  // The full tag catalog, fetched lazily on the first right-click rather
  // than eagerly on mount -- most gallery views never open this menu, so
  // there's no reason to fetch it before it's needed. null means "not
  // fetched yet".
  const [catalogTags, setCatalogTags] = useState(null);
  // Keyed by photo id; a key's absence means "not fetched yet" for that
  // photo (distinct from an empty array, "fetched, no tags"). Reset and
  // re-fetched every time the menu opens, so it never shows tags that were
  // changed elsewhere (the photo modal) since.
  const [tagsByPhoto, setTagsByPhoto] = useState({});
  const [tagActionError, setTagActionError] = useState(null);
  const [tagBusy, setTagBusy] = useState(false);
  const [tripPickerOpen, setTripPickerOpen] = useState(false);

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

  const indexById = useMemo(() => new Map(photos.map((photo, index) => [photo.id, index])), [photos]);

  // Select anchor..target (inclusive) from the flat list, in either direction.
  function rangeIds(anchorId, targetId) {
    const from = indexById.get(anchorId);
    const to = indexById.get(targetId);
    if (from === undefined || to === undefined) return new Set([targetId]);
    const [low, high] = from < to ? [from, to] : [to, from];
    return new Set(photos.slice(low, high + 1).map((photo) => photo.id));
  }

  // Returns true when the click was a selection gesture (so the caller
  // must not also open the photo).
  function handleSelectGesture(photo, event) {
    const additive = event.ctrlKey || event.metaKey;
    if (event.shiftKey) {
      const current = selectionRef.current;
      const anchor = current.anchor ?? photo.id;
      const range = rangeIds(anchor, photo.id);
      const ids = additive ? new Set([...current.ids, ...range]) : range;
      setSelection({ ids, anchor, focus: photo.id });
      return true;
    }
    if (additive) {
      const ids = new Set(selectionRef.current.ids);
      if (ids.has(photo.id)) ids.delete(photo.id);
      else ids.add(photo.id);
      setSelection({ ids, anchor: photo.id, focus: photo.id });
      return true;
    }
    return false;
  }

  function handleCardSelect(photo, origin, event) {
    if (event && handleSelectGesture(photo, event)) return;
    if (selectionRef.current.ids.size > 0) setSelection({ ids: new Set(), anchor: null, focus: null });
    onSelect(photo, origin);
  }

  // Arrow keys / Escape. Off while the photo modal is open (it has its own
  // arrow handling) and while typing in the chat or any other field.
  useEffect(() => {
    if (!keysEnabled) return;
    function handleKeyDown(event) {
      const target = event.target;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) return;
      const current = selectionRef.current;
      if (current.ids.size === 0) return;

      if (event.key === "Escape") {
        if (tagMenu) return; // the menu's own Escape handler closes just that
        setSelection({ ids: new Set(), anchor: null, focus: null });
        return;
      }

      const grid = galleryRef.current?.querySelector(".photo-grid");
      const columns = grid ? getComputedStyle(grid).gridTemplateColumns.split(" ").filter(Boolean).length || 1 : 1;
      const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -columns, ArrowDown: columns }[event.key];
      if (step === undefined || event.ctrlKey || event.metaKey || event.altKey) return;

      event.preventDefault();
      const from = indexById.get(current.focus);
      if (from === undefined) return;
      const to = Math.max(0, Math.min(photos.length - 1, from + step));
      const targetPhoto = photos[to];
      // Make sure the destination has been rendered (infinite-scroll window).
      if (to >= shownCount) setVisibleCount(to + 1);
      if (event.shiftKey) {
        const anchor = current.anchor ?? current.focus;
        setSelection({ ids: rangeIds(anchor, targetPhoto.id), anchor, focus: targetPhoto.id });
      } else {
        setSelection({ ids: new Set([targetPhoto.id]), anchor: targetPhoto.id, focus: targetPhoto.id });
      }
      requestAnimationFrame(() => {
        const element = galleryRef.current?.querySelector(`[data-photo-id="${CSS.escape(targetPhoto.id)}"]`);
        element?.scrollIntoView?.({ block: "nearest" });
      });
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  });

  function handleContextMenu(event, photo) {
    event.preventDefault();
    let photoIds;
    if (selectionRef.current.ids.has(photo.id)) {
      photoIds = [...selectionRef.current.ids];
    } else {
      photoIds = [photo.id];
      if (selectionRef.current.ids.size > 0) setSelection({ ids: new Set(), anchor: null, focus: null });
    }
    setTagMenu({ photoIds, x: event.clientX, y: event.clientY });
    setTripPickerOpen(false);
    setTagActionError(null);
    setTagsByPhoto({});
    if (catalogTags === null) {
      fetchTags()
        .then(setCatalogTags)
        .catch((err) => setTagActionError(err.message));
    }
    mapLimited(photoIds, TAG_CONCURRENCY, async (id) => {
      const tags = await fetchPhotoTags(id);
      setTagsByPhoto((prev) => ({ ...prev, [id]: tags }));
    }).then((results) => {
      const failed = results.find((result) => !result.ok);
      if (failed) setTagActionError(failed.error.message);
    });
  }

  // Toggle `tag` across the menu's photos. If every one of them already
  // has it, it's removed from all of them; otherwise (none, or only some)
  // it's added to the ones that lack it.
  async function handleToggleTag(photoIds, tag) {
    if (tagBusy) return;
    const hasTag = (id) => (tagsByPhoto[id] ?? []).some((candidate) => candidate.id === tag.id);
    const removing = photoIds.every(hasTag);
    const targets = photoIds.filter((id) => (removing ? hasTag(id) : !hasTag(id)));
    if (targets.length === 0) return;
    setTagBusy(true);
    const results = await mapLimited(targets, TAG_CONCURRENCY, async (id) => {
      if (removing) await untagPhoto(id, tag.id);
      else await tagPhoto(id, tag.id);
      setTagsByPhoto((prev) => {
        const existing = prev[id] ?? [];
        const next = removing
          ? existing.filter((candidate) => candidate.id !== tag.id)
          : [...existing, tag].sort((a, b) => a.name.localeCompare(b.name));
        return { ...prev, [id]: next };
      });
    });
    setTagBusy(false);
    const failures = results.filter((result) => !result.ok);
    setTagActionError(
      failures.length === 0
        ? null
        : failures.length === 1
          ? failures[0].error.message
          : `${failures.length} of ${targets.length} photos failed: ${failures[0].error.message}`
    );
  }

  if (photos.length === 0) {
    return <p className="empty-state">{emptyMessage}</p>;
  }

  // Runs a trip action for the menu's photos, then closes the menu.
  async function runTripAction(action) {
    try {
      await action();
      setTagMenu(null);
    } catch (err) {
      setTagActionError(err.message);
    }
  }

  const addTargets = trips.filter((trip) => trip.id !== currentTripId);
  const photoTagsLoaded = tagMenu && tagMenu.photoIds.every((id) => id in tagsByPhoto);

  function renderCards(start, end) {
    return photos.slice(start, Math.min(end, shownCount)).map((photo) => (
      <PhotoCard
        key={photo.id}
        photo={photo}
        selected={selection.ids.has(photo.id)}
        onSelect={handleCardSelect}
        onContextMenu={(event) => handleContextMenu(event, photo)}
      />
    ));
  }

  return (
    <div className="photo-gallery" ref={galleryRef}>
      {sections ? (
        sections
          .filter((section) => section.start < shownCount)
          .map((section) => {
            const sectionId = `${section.key}@${section.start}`;
            return (
            <section key={sectionId} data-section-id={sectionId}>
              <h2 className="photo-section-header">
                {onToggleSidebar && stuckSectionId === sectionId && (
                  <button
                    type="button"
                    className="sidebar-toggle sidebar-toggle-inline"
                    onClick={onToggleSidebar}
                    aria-label="Toggle sidebar"
                    aria-expanded={sidebarOpen}
                  >
                    <span className="sidebar-toggle-icon" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </span>
                  </button>
                )}
                {section.label}
                <span className="photo-section-count">{(section.end - section.start).toLocaleString()}</span>
              </h2>
              <div className="photo-grid">{renderCards(section.start, section.end)}</div>
            </section>
            );
          })
      ) : (
        <div className="photo-grid">{renderCards(0, photos.length)}</div>
      )}
      {hasMore && <div ref={sentinelRef} className="photo-grid-sentinel" aria-hidden="true" />}

      {tagMenu && (
        <div className="tag-menu" style={{ left: tagMenu.x, top: tagMenu.y }} onClick={(event) => event.stopPropagation()}>
          {tagMenu.photoIds.length > 1 && (
            <div className="tag-menu-item tag-menu-message">{tagMenu.photoIds.length} photos selected</div>
          )}
          {catalogTags === null || !photoTagsLoaded ? (
            <div className="tag-menu-item tag-menu-message">Loading…</div>
          ) : catalogTags.length === 0 ? (
            <div className="tag-menu-item tag-menu-message">No tags catalogued yet</div>
          ) : (
            catalogTags.map((tag) => {
              const count = tagMenu.photoIds.filter((id) =>
                (tagsByPhoto[id] ?? []).some((candidate) => candidate.id === tag.id)
              ).length;
              const all = count === tagMenu.photoIds.length;
              const some = count > 0 && !all;
              return (
                <div
                  key={tag.id}
                  className={`tag-menu-item${all ? " tag-menu-item-checked" : ""}`}
                  aria-checked={all ? "true" : some ? "mixed" : "false"}
                  onClick={() => handleToggleTag(tagMenu.photoIds, tag)}
                >
                  <span className="tag-menu-item-check">{all ? "✓" : some ? "–" : ""}</span>
                  {tag.name}
                </div>
              );
            })
          )}
          {(currentTripId !== null || (onAddToTrip && addTargets.length > 0)) && (
            <div className="tag-menu-separator" role="separator" />
          )}
          {currentTripId !== null && onRemoveFromTrip && (
            <div className="tag-menu-item" onClick={() => runTripAction(() => onRemoveFromTrip(tagMenu.photoIds))}>
              Remove from this trip
            </div>
          )}
          {currentTripId !== null && onSetTripCover && tagMenu.photoIds.length === 1 && (
            <div className="tag-menu-item" onClick={() => runTripAction(() => onSetTripCover(tagMenu.photoIds[0]))}>
              Use as the trip&apos;s cover
            </div>
          )}
          {onAddToTrip && addTargets.length > 0 && (
            <>
              <div
                className="tag-menu-item"
                aria-expanded={tripPickerOpen}
                onClick={() => setTripPickerOpen((prev) => !prev)}
              >
                Add to trip…
              </div>
              {tripPickerOpen && (
                <div className="tag-menu-trips">
                  {addTargets.map((trip) => (
                    <div
                      key={trip.id}
                      className="tag-menu-item tag-menu-trip"
                      onClick={() => runTripAction(() => onAddToTrip(trip.id, tagMenu.photoIds))}
                    >
                      {trip.name}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
          {tagActionError && <div className="tag-menu-item tag-menu-error">{tagActionError}</div>}
        </div>
      )}
    </div>
  );
}
