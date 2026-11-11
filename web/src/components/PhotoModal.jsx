import { useEffect, useRef, useState } from "react";
import {
  createIndividual,
  fetchFaces,
  fetchIndividuals,
  fetchPhotoTags,
  fetchTags,
  individualThumbnailUrl,
  originalUrl,
  thumbnailUrl,
  tagFace,
  tagPhoto,
  untagPhoto,
} from "../api/client.js";
import PhotoChat from "./PhotoChat.jsx";
import PhotoMap from "./PhotoMap.jsx";

// Computes the <img>'s actual *visible* image content box -- which, under
// object-fit: contain, can be smaller than the <img> element's own box
// (letterboxed) when the element's aspect ratio doesn't match the photo's.
// Box overlays positioned directly from the element's own box would be
// offset by whatever letterboxing is present, hence this.
//
// Deliberately uses offsetWidth/offsetHeight/offsetLeft/offsetTop, not
// getBoundingClientRect() -- those offset* properties are the element's
// intrinsic *layout* box, untouched by the CSS `transform: scale()`
// hover-zoom (.photo-modal-image-zoomed) applies to this exact element,
// whereas getBoundingClientRect() reports the *painted*, transformed box.
// An earlier version used getBoundingClientRect() and tried to dodge the
// transform with timing (skip measuring while zoomed, re-measure once it
// ends) -- that still broke, because ending the zoom animates back over
// styles.css's `transition: transform 0.1s ease-out` rather than
// snapping instantly, so a measurement taken right as the zoom ends (or
// starts) could still land mid-transition and capture a partially-scaled
// rect. offset* isn't just better-timed, it's unaffected by `transform`
// (a paint-time effect, not a layout one) at any point in that
// transition, so there's no timing to get right in the first place.
// `img`'s offsetParent is `wrap` itself (position: relative, see
// styles.css), so these are already relative to it, matching what the
// absolutely-positioned box overlays need -- `wrap` itself is never
// passed to or read here.
function measureVisibleImageRect(img) {
  if (!img.naturalWidth || !img.naturalHeight) return null;
  const elWidth = img.offsetWidth;
  const elHeight = img.offsetHeight;
  const elAspect = elWidth / elHeight;
  const imgAspect = img.naturalWidth / img.naturalHeight;

  let width, height;
  if (imgAspect > elAspect) {
    // Image is relatively wider than its box -- letterboxed top/bottom.
    width = elWidth;
    height = width / imgAspect;
  } else {
    // Image is relatively taller than its box -- letterboxed left/right.
    height = elHeight;
    width = height * imgAspect;
  }

  return {
    left: img.offsetLeft + (elWidth - width) / 2,
    top: img.offsetTop + (elHeight - height) / 2,
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

// How many neighbours the filmstrip shows on each side of the open photo.
const FILMSTRIP_RADIUS = 7;

// `photos`/`onNavigate` are optional and together enable stepping to the
// neighbouring photos (arrow keys, the edge buttons, the filmstrip):
// `photos` is the ordered list being browsed and `onNavigate(photo)` asks
// the parent to make another one of them the open photo. Without them --
// or when the open photo isn't in that list (e.g. opened from the Manage
// page while the gallery is filtered to something else) -- the modal
// shows just the one photo, as it always did. `origin` ({x, y} in viewport
// px, optional) is where the open animation scales up from; see
// styles.css's .photo-modal.
export default function PhotoModal({ photo, onClose, photos = null, onNavigate = null, origin = null }) {
  const photoIndex = photo && photos && onNavigate ? photos.findIndex((candidate) => candidate.id === photo.id) : -1;
  const previousPhoto = photoIndex > 0 ? photos[photoIndex - 1] : null;
  const nextPhoto = photoIndex !== -1 && photoIndex < photos.length - 1 ? photos[photoIndex + 1] : null;

  // Box(es) located by the florence chat backend for the current turn --
  // drawn as a temporary overlay on the image, never baked into any stored
  // image data (see chat.ChatReply; this is purely a frontend annotation).
  const [boxes, setBoxes] = useState([]);
  const imgRef = useRef(null);
  const modalRef = useRef(null);

  // Hover-zoom (magnifier): wherever the cursor sits over the image, that
  // point becomes the CSS `transform-origin` for a scale-up on the <img>
  // itself -- the point under the cursor stays put while everything around
  // it grows, which is what makes it read as "zooming in on this spot"
  // rather than just enlarging the whole image. `null` means not hovering
  // (image at 1x). Percentages are relative to the <img> element's own
  // border box (matching `transform-origin`'s own coordinate system), not
  // the letterboxed visible-content box `imgRect` tracks below -- object-
  // fit: contain doesn't change under a transform, so the rendered box
  // (letterboxing included) just magnifies as a single image, the same way
  // a real magnifying glass would held over a printed photo.
  const [zoomOrigin, setZoomOrigin] = useState(null);

  // Mirrors the Fullscreen API's actual state (not just "did we call
  // requestFullscreen") so pressing Esc -- which browsers intercept to exit
  // fullscreen natively, bypassing this component entirely -- still flips
  // the button's label back.
  const [isFullscreen, setIsFullscreen] = useState(false);
  // The <img>'s rendered visible-content box, in px relative to the image
  // wrap div (`measureVisibleImageRect`'s offsetParent) -- re-measured on
  // load and on resize (see useEffect below). Box overlays
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

  // Whole-photo tags (tags.py/routers/tags.py -- distinct from the face
  // tagging above). `photoTags` is this photo's own tags; `catalogTags`
  // is the full tag catalog, fetched lazily to populate the "add a
  // tag…" dropdown below -- both null means "not fetched yet", same
  // convention as faceBoxes/individuals above.
  const [photoTags, setPhotoTags] = useState(null);
  const [catalogTags, setCatalogTags] = useState(null);
  const [tagToAdd, setTagToAdd] = useState("");
  const [tagActionError, setTagActionError] = useState(null);

  function setTagMenu(updater) {
    setTagMenuState((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      tagMenuRef.current = next;
      return next;
    });
  }

  function updateImgRect() {
    // No need to worry about hover-zoom's transform here at all -- see
    // measureVisibleImageRect's own docstring for why offset*-based
    // measurement is immune to it regardless of timing.
    if (imgRef.current) {
      setImgRect(measureVisibleImageRect(imgRef.current));
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
    function handleFullscreenChange() {
      setIsFullscreen(document.fullscreenElement === modalRef.current);
    }
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);

  // Fullscreen is exited here on unmount rather than relying on the browser
  // to do it -- closing the modal removes modalRef.current from the DOM
  // while it may still be the active fullscreen element, which would
  // otherwise leave the user staring at a blank fullscreen layer with
  // nothing left to show.
  useEffect(() => {
    return () => {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    };
  }, []);

  // A context that doesn't grant the "fullscreen" permission (e.g. an
  // iframe without allow="fullscreen", or a tab under remote/automated
  // control) can make request/exitFullscreen() either reject their Promise
  // or throw synchronously, depending on the browser -- caught both ways
  // since there's nothing more to do about it here than not surface it as
  // an unhandled exception; isFullscreen just stays whatever it already was
  // (no fullscreenchange event fires either way).
  function toggleFullscreen() {
    try {
      if (document.fullscreenElement) {
        document.exitFullscreen()?.catch(() => {});
      } else {
        modalRef.current?.requestFullscreen()?.catch(() => {});
      }
    } catch {
      // See comment above.
    }
  }

  function handleImageMouseMove(event) {
    // Boxes (chat grounding or stored faces) are positioned from imgRect,
    // which isn't re-measured on every mousemove -- scaling the image out
    // from under them while zoomed would desync the overlay from what
    // they're supposed to be pointing at, so hover-zoom sits out while
    // either kind of box is on screen.
    if (canShowBoxes || canShowFaces) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setZoomOrigin({
      x: ((event.clientX - rect.left) / rect.width) * 100,
      y: ((event.clientY - rect.top) / rect.height) * 100,
    });
  }

  function handleImageMouseLeave() {
    setZoomOrigin(null);
  }

  useEffect(() => {
    setShowFaces(false);
    setFaceBoxes(null);
    setFacesError(null);
    setTagMenu(null);
    setTagError(null);
    setPhotoTags(null);
    setTagToAdd("");
    setTagActionError(null);
    setZoomOrigin(null);
  }, [photo?.id]);

  useEffect(() => {
    if (!photo) return;

    function handleKeyDown(event) {
      // Don't hijack keys while the user is typing a chat message (or in
      // any other editable control inside the modal) -- "f" is a letter,
      // and the arrow keys move the text cursor / change a <select>.
      const target = event.target;
      const isEditable =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);
      if (isEditable) return;

      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        const destination = event.key === "ArrowLeft" ? previousPhoto : nextPhoto;
        if (destination) {
          event.preventDefault();
          onNavigate(destination);
        }
        return;
      }
      if (event.key === "Escape") {
        // In fullscreen, Esc belongs to the browser (it exits fullscreen);
        // only a second press, back in the windowed modal, closes it.
        if (!document.fullscreenElement) onClose();
        return;
      }
      if (event.key.toLowerCase() !== "f") return;

      // Face boxes are positioned from imgRect, not the live (possibly
      // scaled) image -- leaving a hover-zoom engaged while they're shown
      // would both desync the boxes from the magnified image and make
      // them hard to read, so toggling faces always drops back to 1x.
      setZoomOrigin(null);
      setShowFaces((prev) => !prev);
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [photo, previousPhoto, nextPhoto, onNavigate, onClose]);

  // Same reasoning as the "f" handler above, for the other source of box
  // overlays: a chat reply landing while the image is still zoomed in from
  // an earlier hover.
  useEffect(() => {
    if (boxes.length > 0) setZoomOrigin(null);
  }, [boxes]);

  // Fetched as soon as a photo is open, not gated on showFaces -- the
  // "People in this photo" panel below is always visible (unlike the
  // bounding-box overlay and the catalog-wide "People" panel further
  // down, both still gated on showFaces), so it needs this data
  // regardless of whether the user ever presses "f".
  useEffect(() => {
    if (!photo || faceBoxes !== null) return;
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
  }, [photo, faceBoxes]);

  useEffect(() => {
    if (!photo || individuals !== null) return;
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
  }, [photo, individuals]);

  // This photo's own tags -- fetched eagerly as soon as it opens, same
  // "always visible" reasoning as faceBoxes above (the Tags panel isn't
  // gated behind any toggle).
  useEffect(() => {
    if (!photo || photoTags !== null) return;
    let cancelled = false;
    fetchPhotoTags(photo.id)
      .then((tags) => {
        if (!cancelled) setPhotoTags(tags);
      })
      .catch((err) => {
        if (!cancelled) setTagActionError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [photo, photoTags]);

  // The full tag catalog, for the "add a tag…" dropdown -- fetched once
  // (not reset on photo change, unlike photoTags above) and reused across
  // every photo opened this session.
  useEffect(() => {
    if (!photo || catalogTags !== null) return;
    let cancelled = false;
    fetchTags()
      .then((list) => {
        if (!cancelled) setCatalogTags(list);
      })
      .catch((err) => {
        if (!cancelled) setTagActionError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [photo, catalogTags]);

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

  function handleAddTag(event) {
    event.preventDefault();
    if (!tagToAdd) return;
    const tag = catalogTags?.find((candidate) => candidate.id === tagToAdd);
    tagPhoto(photo.id, tagToAdd)
      .then(() => {
        if (tag) {
          setPhotoTags((prev) => [...(prev ?? []), tag].sort((a, b) => a.name.localeCompare(b.name)));
        }
        setTagToAdd("");
        setTagActionError(null);
      })
      .catch((err) => setTagActionError(err.message));
  }

  function handleRemoveTag(tagId) {
    untagPhoto(photo.id, tagId)
      .then(() => {
        setPhotoTags((prev) => (prev ?? []).filter((tag) => tag.id !== tagId));
        setTagActionError(null);
      })
      .catch((err) => setTagActionError(err.message));
  }

  if (!photo) return null;

  const canShowBoxes = boxes.length > 0 && photo.width && photo.height && imgRect;
  const canShowFaces = showFaces && faceBoxes && faceBoxes.length > 0 && photo.width && photo.height && imgRect;

  // Distinct individuals present in this photo -- one entry per person_id
  // across faceBoxes, whether tagged manually or auto-matched (people.py's
  // tag_face vs recognition.py's "Refresh Faces" -- both set person_id the
  // same way, so no special-casing is needed here beyond the tag_source
  // badge below). Keeps the *first* box seen per person for its
  // tag_source -- a person is realistically tagged the same way on every
  // face within one photo, so which one wins doesn't matter in practice.
  const peopleInPhoto = faceBoxes
    ? Array.from(new Map(faceBoxes.filter((box) => box.person_id).map((box) => [box.person_id, box])).values())
    : [];

  // Catalog tags not already applied to this photo -- what the "add a
  // tag…" dropdown offers. New tags themselves are only ever created via
  // ManagementPage, not here.
  const availableTagsToAdd = (catalogTags ?? []).filter(
    (tag) => !(photoTags ?? []).some((applied) => applied.id === tag.id)
  );

  const filmstripStart = Math.max(0, photoIndex - FILMSTRIP_RADIUS);
  const filmstrip = photoIndex === -1 ? [] : photos.slice(filmstripStart, photoIndex + FILMSTRIP_RADIUS + 1);

  return (
    <div
      className="photo-modal-backdrop"
      onClick={onClose}
      style={origin ? { "--modal-origin-x": `${origin.x}px`, "--modal-origin-y": `${origin.y}px` } : undefined}
    >
      <div className="photo-modal" ref={modalRef} onClick={(event) => event.stopPropagation()}>
        <div className="photo-modal-toolbar">
          <button type="button" className="close-button" onClick={toggleFullscreen}>
            {isFullscreen ? "Exit full screen" : "Full screen"}
          </button>
          <a
            className="close-button"
            href={originalUrl(photo.id)}
            download={photo.filename.split("/").pop()}
          >
            Download
          </a>
          <button type="button" className="close-button" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="photo-modal-image-pane">
          <div className="photo-modal-image-wrap">
            <img
              // Keyed so each photo gets a fresh element: the fade-in
              // replays, and the previous photo doesn't linger on screen
              // while the next original is still downloading.
              key={photo.id}
              ref={imgRef}
              src={originalUrl(photo.id)}
              alt={photo.filename}
              onLoad={updateImgRect}
              onMouseMove={handleImageMouseMove}
              onMouseLeave={handleImageMouseLeave}
              className={zoomOrigin ? "photo-modal-image-zoomed" : undefined}
              style={
                zoomOrigin
                  ? { transformOrigin: `${zoomOrigin.x}% ${zoomOrigin.y}%` }
                  : undefined
              }
            />
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
                    {box.detection_confidence != null && (
                      <span className="photo-modal-bbox-score">
                        {Math.round(box.detection_confidence * 100)}%
                      </span>
                    )}
                  </div>
                );
              })}
            {previousPhoto && (
              <button
                type="button"
                className="photo-modal-nav photo-modal-nav-prev"
                onClick={() => onNavigate(previousPhoto)}
                aria-label="Previous photo"
              >
                <span aria-hidden="true">&lsaquo;</span>
              </button>
            )}
            {nextPhoto && (
              <button
                type="button"
                className="photo-modal-nav photo-modal-nav-next"
                onClick={() => onNavigate(nextPhoto)}
                aria-label="Next photo"
              >
                <span aria-hidden="true">&rsaquo;</span>
              </button>
            )}
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
          {filmstrip.length > 1 && (
            <div className="photo-modal-filmstrip">
              {filmstrip.map((candidate) => (
                <button
                  key={candidate.id}
                  type="button"
                  className={`photo-modal-filmstrip-item${candidate.id === photo.id ? " photo-modal-filmstrip-item-current" : ""}`}
                  onClick={() => onNavigate(candidate)}
                  aria-label={`View ${candidate.filename}`}
                  aria-current={candidate.id === photo.id ? "true" : undefined}
                >
                  <img src={thumbnailUrl(candidate.id)} alt="" loading="lazy" />
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="photo-modal-sidebar">
          <div className="photo-modal-meta">
            <h2>{photo.filename}</h2>
            <p>
              {photo.width} &times; {photo.height}
            </p>
            <p>{new Date(photo.created_at).toLocaleString()}</p>
            {photo.location_name && <p className="photo-modal-location">{photo.location_name}</p>}
            {photo.description && <p className="photo-modal-description">{photo.description}</p>}
          </div>

          <div className="photo-modal-people-in-photo">
            <h3>People in this photo</h3>
            {faceBoxes === null ? (
              <p className="photo-modal-people-loading">Loading…</p>
            ) : peopleInPhoto.length === 0 ? (
              <p className="photo-modal-people-empty">No one identified in this photo yet.</p>
            ) : (
              <ul className="photo-modal-people-in-photo-list">
                {peopleInPhoto.map((box) => {
                  const individual = individuals?.find((candidate) => candidate.id === box.person_id);
                  return (
                    <li key={box.person_id}>
                      {individual?.has_representative_thumbnail && (
                        <img
                          src={individualThumbnailUrl(box.person_id)}
                          alt=""
                          className="photo-modal-people-thumb"
                        />
                      )}
                      <span>{individual?.name ?? "Unknown"}</span>
                      {box.tag_source === "auto" && (
                        <span className="photo-modal-auto-badge">auto-detected</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="photo-modal-tags">
            <h3>Tags</h3>
            {photoTags === null ? (
              <p className="photo-modal-tags-loading">Loading…</p>
            ) : photoTags.length === 0 ? (
              <p className="photo-modal-tags-empty">No tags on this photo yet.</p>
            ) : (
              <ul className="photo-modal-tags-list">
                {photoTags.map((tag) => (
                  <li key={tag.id} className="photo-modal-tag-chip">
                    <span>{tag.name}</span>
                    <button
                      type="button"
                      onClick={() => handleRemoveTag(tag.id)}
                      aria-label={`Remove tag ${tag.name}`}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {catalogTags !== null && catalogTags.length === 0 ? (
              <p className="photo-modal-tags-empty">
                No tags catalogued yet -- add one from the Manage page.
              </p>
            ) : (
              availableTagsToAdd.length > 0 && (
                <form className="photo-modal-add-tag" onSubmit={handleAddTag}>
                  <select value={tagToAdd} onChange={(event) => setTagToAdd(event.target.value)}>
                    <option value="">Add a tag…</option>
                    {availableTagsToAdd.map((tag) => (
                      <option key={tag.id} value={tag.id}>
                        {tag.name}
                      </option>
                    ))}
                  </select>
                  <button type="submit" disabled={!tagToAdd}>
                    Add
                  </button>
                </form>
              )
            )}
            {tagActionError && (
              <p role="alert" className="photo-modal-tags-error">
                {tagActionError}
              </p>
            )}
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
