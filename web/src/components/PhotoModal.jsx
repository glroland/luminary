import { useEffect, useRef, useState } from "react";
import {
  createIndividual,
  fetchFaces,
  fetchIndividuals,
  individualThumbnailUrl,
  originalUrl,
  tagFace,
} from "../api/client.js";
import PhotoChat from "./PhotoChat.jsx";
import PhotoMap from "./PhotoMap.jsx";

// Computes the <img>'s actual *visible* image content box -- which, under
// object-fit: contain, can be smaller than the <img> element's own box
// (letterboxed) when the element's aspect ratio doesn't match the photo's.
// getBoundingClientRect() on the <img> itself would report the element's
// box, not this, so box overlays positioned from that directly would be
// offset by whatever letterboxing is present. Returned rect is relative to
// `wrap` (the positioned ancestor box overlays are placed against).
function measureVisibleImageRect(img, wrap) {
  if (!img.naturalWidth || !img.naturalHeight) return null;
  const elRect = img.getBoundingClientRect();
  const wrapRect = wrap.getBoundingClientRect();
  const elAspect = elRect.width / elRect.height;
  const imgAspect = img.naturalWidth / img.naturalHeight;

  let width, height;
  if (imgAspect > elAspect) {
    // Image is relatively wider than its box -- letterboxed top/bottom.
    width = elRect.width;
    height = width / imgAspect;
  } else {
    // Image is relatively taller than its box -- letterboxed left/right.
    height = elRect.height;
    width = height * imgAspect;
  }

  return {
    left: elRect.left - wrapRect.left + (elRect.width - width) / 2,
    top: elRect.top - wrapRect.top + (elRect.height - height) / 2,
    width,
    height,
  };
}

// Converts a box in the photo's own pixel coordinate space (x1/y1/x2/y2,
// matching photo.width/height) into inline CSS for an overlay positioned
// against imgRect (see measureVisibleImageRect) -- shared by the chat
// grounding boxes and the stored-faces overlay below, which otherwise
// differ only in styling/source.
function boxStyle(box, photo, imgRect) {
  return {
    left: `${imgRect.left + (box.x1 / photo.width) * imgRect.width}px`,
    top: `${imgRect.top + (box.y1 / photo.height) * imgRect.height}px`,
    width: `${((box.x2 - box.x1) / photo.width) * imgRect.width}px`,
    height: `${((box.y2 - box.y1) / photo.height) * imgRect.height}px`,
  };
}

export default function PhotoModal({ photo, onClose }) {
  // Box(es) located by the florence chat backend for the current turn --
  // drawn as a temporary overlay on the image, never baked into any stored
  // image data (see chat.ChatReply; this is purely a frontend annotation).
  const [boxes, setBoxes] = useState([]);
  const imgRef = useRef(null);
  const wrapRef = useRef(null);
  // The <img>'s rendered visible-content box, in px relative to wrapRef --
  // re-measured on load and on resize (see useEffect below). Box overlays
  // are positioned in pixels from this rather than CSS percentages of the
  // wrap: percentage *heights* only resolve against an ancestor with a
  // definite height, and object-fit: contain's letterboxing means the
  // <img> element's own box isn't necessarily the *visible* image's box
  // either -- pixel math from a JS measurement sidesteps both problems.
  const [imgRect, setImgRect] = useState(null);

  // Detected-face overlay, toggled by pressing "f" while the modal is open
  // -- stored faces (sync.py's Florence-2 face-detection pass), not
  // anything from the live chat turn above. `null` means "not fetched
  // yet for this photo" (distinct from `[]`, "fetched, no faces found"),
  // so toggling off and back on doesn't re-fetch.
  const [showFaces, setShowFaces] = useState(false);
  const [faceBoxes, setFaceBoxes] = useState(null);
  const [facesError, setFacesError] = useState(null);

  // Manual face-tagging (catalog of named individuals + assigning a face
  // to one -- see people.py/routers/faces.py). `individuals` is fetched
  // lazily the same way faceBoxes is: null means "not fetched yet",
  // distinct from "[]", fetched but empty.
  const [individuals, setIndividuals] = useState(null);
  const [newPersonName, setNewPersonName] = useState("");
  const [addPersonError, setAddPersonError] = useState(null);
  const [tagError, setTagError] = useState(null);

  // The in-progress right-mouse-drag tag menu: {faceId, x, y, hoveredId}
  // while a face box's right (secondary) button is held down, else null.
  // Right-click rather than middle-click: a Mac trackpad's two-finger
  // click is a secondary click at the OS/DOM level (button 2, firing
  // "contextmenu"), never a true middle click (button 1) -- there's no
  // trackpad gesture that produces that at all, only a physical mouse's
  // scroll-wheel click does. `x`/`y` are viewport coordinates (the
  // mousedown's clientX/clientY) since the menu
  // is positioned with `position: fixed`. Mirrored into tagMenuRef so the
  // window "mouseup" listener (attached/detached only when the menu opens/
  // closes, not on every hover change -- see the effect below) always
  // reads the latest hoveredId without needing to be re-attached per
  // mousemove.
  const [tagMenu, setTagMenuState] = useState(null);
  const tagMenuRef = useRef(null);

  function setTagMenu(updater) {
    setTagMenuState((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      tagMenuRef.current = next;
      return next;
    });
  }

  function updateImgRect() {
    if (imgRef.current && wrapRef.current) {
      setImgRect(measureVisibleImageRect(imgRef.current, wrapRef.current));
    }
  }

  useEffect(() => {
    setImgRect(null);
    const img = imgRef.current;
    if (!img) return;
    // Covers window/modal resize while open; onLoad (below) covers the
    // initial load, which a ResizeObserver attached post-mount can miss if
    // the image loads from cache before the observer is attached.
    const observer = new ResizeObserver(updateImgRect);
    observer.observe(img);
    if (img.complete) updateImgRect();
    return () => observer.disconnect();
  }, [photo?.id]);

  useEffect(() => {
    setShowFaces(false);
    setFaceBoxes(null);
    setFacesError(null);
    setTagMenu(null);
    setTagError(null);
  }, [photo?.id]);

  useEffect(() => {
    if (!photo) return;

    function handleKeyDown(event) {
      if (event.key.toLowerCase() !== "f") return;
      // Don't hijack "f" while the user is typing a chat message (or any
      // other editable control inside the modal).
      const target = event.target;
      const isEditable =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (isEditable) return;

      setShowFaces((prev) => !prev);
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [photo]);

  useEffect(() => {
    if (!photo || !showFaces || faceBoxes !== null) return;
    let cancelled = false;
    fetchFaces(photo.id)
      .then((faces) => {
        if (!cancelled) setFaceBoxes(faces);
      })
      .catch((err) => {
        if (!cancelled) setFacesError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [photo, showFaces, faceBoxes]);

  useEffect(() => {
    if (!showFaces || individuals !== null) return;
    let cancelled = false;
    fetchIndividuals()
      .then((list) => {
        if (!cancelled) setIndividuals(list);
      })
      .catch((err) => {
        if (!cancelled) setTagError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [showFaces, individuals]);

  const menuOpen = tagMenu !== null;
  useEffect(() => {
    if (!menuOpen) return;

    function handleMouseUp(event) {
      if (event.button !== 2) return;
      const current = tagMenuRef.current;
      setTagMenu(null);
      if (!current?.hoveredId) return;
      tagFace(current.faceId, current.hoveredId)
        .then((updated) => {
          setFaceBoxes((prev) =>
            prev ? prev.map((box) => (box.id === updated.id ? { ...box, person_id: updated.person_id } : box)) : prev
          );
          setTagError(null);
        })
        .catch((err) => setTagError(err.message));
    }

    window.addEventListener("mouseup", handleMouseUp);
    return () => window.removeEventListener("mouseup", handleMouseUp);
  }, [menuOpen]);

  function handleFaceMouseDown(event, faceId) {
    if (event.button !== 2) return;
    // The actual menu-open signal is the "contextmenu" event below -- this
    // preventDefault just stops any other native right-click handling on
    // mousedown itself, mirroring handleFaceContextMenu.
    event.preventDefault();
    setTagMenu({ faceId, x: event.clientX, y: event.clientY, hoveredId: null });
    setTagError(null);
  }

  function handleFaceContextMenu(event) {
    // Suppresses the browser's native right-click menu on a face box only
    // -- scoped to this element (not the whole modal/image) so right-click
    // still works normally everywhere else, e.g. to save the photo.
    event.preventDefault();
  }

  function handleAddIndividual(event) {
    event.preventDefault();
    const name = newPersonName.trim();
    if (!name) return;
    createIndividual(name)
      .then((created) => {
        setIndividuals((prev) => [...(prev ?? []), created].sort((a, b) => a.name.localeCompare(b.name)));
        setNewPersonName("");
        setAddPersonError(null);
      })
      .catch((err) => setAddPersonError(err.message));
  }

  if (!photo) return null;

  const canShowBoxes = boxes.length > 0 && photo.width && photo.height && imgRect;
  const canShowFaces = showFaces && faceBoxes && faceBoxes.length > 0 && photo.width && photo.height && imgRect;

  return (
    <div className="photo-modal-backdrop" onClick={onClose}>
      <div className="photo-modal" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="close-button" onClick={onClose}>
          Close
        </button>

        <div className="photo-modal-image-pane">
          <div className="photo-modal-image-wrap" ref={wrapRef}>
            <img ref={imgRef} src={originalUrl(photo.id)} alt={photo.filename} onLoad={updateImgRect} />
            {canShowBoxes &&
              boxes.map((box, index) => (
                <div key={index} className="photo-modal-bbox" style={boxStyle(box, photo, imgRect)}>
                  <span className="photo-modal-bbox-label">{box.label}</span>
                </div>
              ))}
            {canShowFaces &&
              faceBoxes.map((box, index) => {
                const taggedName = box.person_id
                  ? individuals?.find((individual) => individual.id === box.person_id)?.name
                  : null;
                return (
                  <div
                    key={box.id ?? index}
                    className={`photo-modal-bbox photo-modal-bbox-face${box.person_id ? " photo-modal-bbox-face-tagged" : ""}`}
                    style={boxStyle(box, photo, imgRect)}
                    onMouseDown={box.id ? (event) => handleFaceMouseDown(event, box.id) : undefined}
                    onContextMenu={box.id ? handleFaceContextMenu : undefined}
                  >
                    {taggedName && <span className="photo-modal-bbox-label">{taggedName}</span>}
                  </div>
                );
              })}
            {showFaces && (
              <p className="photo-modal-faces-hint">
                {facesError
                  ? `Couldn't load faces: ${facesError}`
                  : faceBoxes === null
                    ? "Loading faces…"
                    : faceBoxes.length === 0
                      ? "No faces detected for this photo."
                      : `${faceBoxes.length} face${faceBoxes.length === 1 ? "" : "s"} detected (press F to hide)`}
              </p>
            )}
          </div>
        </div>

        <div className="photo-modal-sidebar">
          <div className="photo-modal-meta">
            <h2>{photo.filename}</h2>
            <p>
              {photo.width} &times; {photo.height}
            </p>
            <p>{new Date(photo.created_at).toLocaleString()}</p>
            {photo.description && <p className="photo-modal-description">{photo.description}</p>}
          </div>

          <PhotoMap latitude={photo.latitude} longitude={photo.longitude} />

          {showFaces && (
            <div className="photo-modal-people">
              <h3>People</h3>
              {individuals === null ? (
                <p className="photo-modal-people-loading">Loading…</p>
              ) : individuals.length === 0 ? (
                <p className="photo-modal-people-empty">
                  No individuals catalogued yet -- add one below, then right-click and hold a face box to tag it.
                </p>
              ) : (
                <ul className="photo-modal-people-list">
                  {individuals.map((individual) => (
                    <li key={individual.id}>
                      {individual.has_representative_thumbnail && (
                        <img
                          src={individualThumbnailUrl(individual.id)}
                          alt=""
                          className="photo-modal-people-thumb"
                        />
                      )}
                      {individual.name}
                    </li>
                  ))}
                </ul>
              )}
              <form className="photo-modal-add-person" onSubmit={handleAddIndividual}>
                <input
                  type="text"
                  value={newPersonName}
                  onChange={(event) => setNewPersonName(event.target.value)}
                  placeholder="Add a person…"
                />
                <button type="submit" disabled={!newPersonName.trim()}>
                  Add
                </button>
              </form>
              {addPersonError && (
                <p role="alert" className="photo-modal-people-error">
                  {addPersonError}
                </p>
              )}
              {tagError && (
                <p role="alert" className="photo-modal-people-error">
                  {tagError}
                </p>
              )}
            </div>
          )}

          <PhotoChat photoId={photo.id} onBoxesChange={setBoxes} />
        </div>
      </div>

      {tagMenu && (
        <div
          className="face-tag-menu"
          style={{ left: tagMenu.x, top: tagMenu.y }}
          onClick={(event) => event.stopPropagation()}
        >
          {individuals === null ? (
            <div className="face-tag-menu-item face-tag-menu-message">Loading…</div>
          ) : individuals.length === 0 ? (
            <div className="face-tag-menu-item face-tag-menu-message">No individuals catalogued yet</div>
          ) : (
            individuals.map((individual) => (
              <div
                key={individual.id}
                className={`face-tag-menu-item${tagMenu.hoveredId === individual.id ? " face-tag-menu-item-active" : ""}`}
                onMouseEnter={() => setTagMenu((prev) => (prev ? { ...prev, hoveredId: individual.id } : prev))}
                onMouseLeave={() =>
                  setTagMenu((prev) => (prev && prev.hoveredId === individual.id ? { ...prev, hoveredId: null } : prev))
                }
              >
                {individual.name}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
