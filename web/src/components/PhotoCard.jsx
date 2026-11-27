import { useEffect, useRef, useState } from "react";
import { thumbnailUrl } from "../api/client.js";
import { formatDuration, isMoving, isVideo } from "../media.js";

// Keep in step with .photo-card-tooltip's width/offset in styles.css.
const TOOLTIP_WIDTH_PX = 280;
const TOOLTIP_GAP_PX = 10;

// Touch has no right-click, so a press held this long (without moving) opens
// the same menu onContextMenu does.
const LONG_PRESS_MS = 500;
const LONG_PRESS_SLOP_PX = 10;

function formatDate(createdAt) {
  const date = new Date(createdAt);
  if (!createdAt || Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// No filename caption: the thumbnail is the whole card. The filename is
// still the image's alt text (which is also what tests locate a card by).
//
// Everything that appears on hover -- the frame, the zoom, the date/
// location strip, and the description tooltip (after a 1s rest) -- is
// always in the DOM and revealed purely by CSS (`.photo-card:hover ...` in
// styles.css). There are no hover timers/state here, so moving the mouse
// across the grid never re-renders anything.
//
// A photo shown as a search result carries `search_score` (0-1, set by
// App.jsx's handleSearchResults); it's shown as a percentage badge in the
// tile's bottom-right corner, always visible rather than hover-only.
//
// A video or animated image carries a video-camera badge in the tile's
// bottom-right corner (always visible); a video also shows its length. A
// search-score badge on the same tile moves up above it.
//
// `loaded` is the one piece of state: it flips once the thumbnail has
// arrived (or failed -- an errored image must not stay invisible) and
// drives the fade-in and the end of the shimmer placeholder. The ref
// callback covers a thumbnail already complete from cache by the time
// React attaches onLoad.
export default function PhotoCard({ photo, onSelect, onContextMenu, selected = false }) {
  const [loaded, setLoaded] = useState(false);
  const date = formatDate(photo.created_at);
  const hasScore = typeof photo.search_score === "number";
  const moving = isMoving(photo);
  const duration = isVideo(photo) ? formatDuration(photo.duration_seconds) : null;
  const pressRef = useRef({ timer: null, x: 0, y: 0 });

  useEffect(() => () => clearTimeout(pressRef.current.timer), []);

  function cancelLongPress() {
    clearTimeout(pressRef.current.timer);
    pressRef.current.timer = null;
  }

  function handlePointerDown(event) {
    if (!onContextMenu || event.pointerType === "mouse") return;
    cancelLongPress();
    const { clientX, clientY } = event;
    pressRef.current.x = clientX;
    pressRef.current.y = clientY;
    pressRef.current.timer = setTimeout(() => {
      pressRef.current.timer = null;
      // Releasing a long press can still produce a click; swallow it so it
      // neither opens the photo nor immediately closes the menu.
      const swallow = (clickEvent) => {
        clickEvent.stopPropagation();
        clickEvent.preventDefault();
        cleanup();
      };
      const cleanup = () => {
        clearTimeout(expiry);
        window.removeEventListener("click", swallow, true);
      };
      const expiry = setTimeout(cleanup, 600);
      window.addEventListener("click", swallow, true);
      onContextMenu({ preventDefault() {}, clientX, clientY });
    }, LONG_PRESS_MS);
  }

  function handlePointerMove(event) {
    if (!pressRef.current.timer) return;
    if (Math.hypot(event.clientX - pressRef.current.x, event.clientY - pressRef.current.y) > LONG_PRESS_SLOP_PX) {
      cancelLongPress();
    }
  }

  function handleClick(event) {
    // The tile's centre, in viewport px: where PhotoModal's open animation
    // scales up from.
    const rect = event.currentTarget.getBoundingClientRect();
    onSelect(photo, { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }, event);
  }

  // The tooltip sits beside the tile: to its right, unless there isn't
  // room before the viewport edge (a tile in the rightmost column), in
  // which case it flips to the left. Written straight onto the element as
  // a data attribute rather than React state, so hovering still never
  // re-renders anything; styles.css keys the placement off it.
  function handleMouseEnter(event) {
    const card = event.currentTarget;
    const rect = card.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
    card.dataset.tooltipSide =
      rect.right + TOOLTIP_GAP_PX + TOOLTIP_WIDTH_PX > viewportWidth ? "left" : "right";
  }

  return (
    <div
      className={`photo-card${loaded ? " photo-card-loaded" : ""}${hasScore ? " photo-card-scored" : ""}${moving ? " photo-card-moving" : ""}${selected ? " photo-card-selected" : ""}`}
      data-photo-id={photo.id}
      aria-selected={selected}
      onContextMenu={onContextMenu}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={cancelLongPress}
      onPointerCancel={cancelLongPress}
      onPointerLeave={cancelLongPress}
      onMouseEnter={handleMouseEnter}
    >
      <div className="photo-card-media" onClick={handleClick}>
        <img
          ref={(img) => {
            if (img?.complete && img.naturalWidth > 0) setLoaded(true);
          }}
          src={thumbnailUrl(photo.id)}
          alt={photo.filename}
          loading="lazy"
          decoding="async"
          onLoad={() => setLoaded(true)}
          onError={() => setLoaded(true)}
        />
        {(date || photo.location_name) && (
          <div className="photo-card-overlay">
            {date && <span className="photo-card-overlay-date">{date}</span>}
            {photo.location_name && <span>{photo.location_name}</span>}
          </div>
        )}
        {selected && (
          <span className="photo-card-check" aria-hidden="true">
            ✓
          </span>
        )}
        {moving && (
          <span
            className="photo-card-motion"
            role="img"
            aria-label={isVideo(photo) ? (duration ? `Video, ${duration}` : "Video") : "Animated image"}
            title={isVideo(photo) ? "Video" : "Animated image"}
          >
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
              <path
                fill="currentColor"
                d="M4 6.5A2.5 2.5 0 0 1 6.5 4h7A2.5 2.5 0 0 1 16 6.5v2.9l4.2-2.6a.8.8 0 0 1 1.2.7v8.9a.8.8 0 0 1-1.2.7L16 14.6v2.9a2.5 2.5 0 0 1-2.5 2.5h-7A2.5 2.5 0 0 1 4 17.5z"
              />
            </svg>
            {duration && <span className="photo-card-motion-duration">{duration}</span>}
          </span>
        )}
        {hasScore && (
          <span className="photo-card-score" title="Match score">
            {Math.round(photo.search_score * 100)}%
          </span>
        )}
      </div>
      <div className="photo-card-tooltip" role="tooltip">
        <div className="photo-card-tooltip-path">{photo.filename}</div>
        {photo.description && <div className="photo-card-tooltip-description">{photo.description}</div>}
      </div>
    </div>
  );
}
