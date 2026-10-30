import { useEffect, useRef, useState } from "react";
import { fetchFaces, originalUrl } from "../api/client.js";
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
              faceBoxes.map((box, index) => (
                <div
                  key={index}
                  className="photo-modal-bbox photo-modal-bbox-face"
                  style={boxStyle(box, photo, imgRect)}
                />
              ))}
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

          <PhotoChat photoId={photo.id} onBoxesChange={setBoxes} />
        </div>
      </div>
    </div>
  );
}
