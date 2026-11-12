import { useState } from "react";
import { thumbnailUrl } from "../api/client.js";

// Keep in step with .photo-card-tooltip's width/offset in styles.css.
const TOOLTIP_WIDTH_PX = 280;
const TOOLTIP_GAP_PX = 10;

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
// `loaded` is the one piece of state: it flips once the thumbnail has
// arrived (or failed -- an errored image must not stay invisible) and
// drives the fade-in and the end of the shimmer placeholder. The ref
// callback covers a thumbnail already complete from cache by the time
// React attaches onLoad.
export default function PhotoCard({ photo, onSelect, onContextMenu }) {
  const [loaded, setLoaded] = useState(false);
  const date = formatDate(photo.created_at);

  function handleClick(event) {
    // The tile's centre, in viewport px: where PhotoModal's open animation
    // scales up from.
    const rect = event.currentTarget.getBoundingClientRect();
    onSelect(photo, { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
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
      className={`photo-card${loaded ? " photo-card-loaded" : ""}`}
      onContextMenu={onContextMenu}
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
      </div>
      <div className="photo-card-tooltip" role="tooltip">
        <div className="photo-card-tooltip-path">{photo.filename}</div>
        {photo.description && <div className="photo-card-tooltip-description">{photo.description}</div>}
      </div>
    </div>
  );
}
