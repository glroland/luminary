// Display rotation (photos.rotation): degrees clockwise, a multiple of
// ROTATION_STEP, 0-359. The API turns the thumbnail and original itself, so
// the only things the web needs are the angle arithmetic and where a box
// drawn on the *upright* photo (face boxes, chat grounding boxes) lands on
// the turned one.

export const ROTATION_STEP = 45;

export function normalizeRotation(degrees) {
  return (((Math.round(degrees) % 360) + 360) % 360) || 0;
}

export function rotatedBy(current, delta) {
  return normalizeRotation((current || 0) + delta);
}

// Only still images can be turned (the API answers 422 otherwise).
export function canRotate(photo) {
  return !!photo && (photo.media_kind ?? "image") === "image";
}

// Size of the canvas an image of width x height needs once turned. A quarter
// turn swaps the sides; other angles grow it to hold the corners (the API
// does the same, with transparent corners).
export function rotatedSize(width, height, degrees) {
  const radians = (normalizeRotation(degrees) * Math.PI) / 180;
  const cos = Math.abs(Math.cos(radians));
  const sin = Math.abs(Math.sin(radians));
  return {
    width: width * cos + height * sin,
    height: width * sin + height * cos,
  };
}

// `box` ({x1,y1,x2,y2} in the upright photo's pixels) as the axis-aligned box
// that encloses it after the photo is turned `degrees` clockwise, in the
// turned canvas's pixels. Exact for quarter turns; an enclosing box otherwise.
// Returns the box plus the turned canvas's size.
export function rotateBox(box, width, height, degrees) {
  const canvas = rotatedSize(width, height, degrees);
  const radians = (normalizeRotation(degrees) * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  const cx = width / 2;
  const cy = height / 2;
  const xs = [];
  const ys = [];
  for (const [x, y] of [
    [box.x1, box.y1],
    [box.x2, box.y1],
    [box.x1, box.y2],
    [box.x2, box.y2],
  ]) {
    // Screen coordinates have y pointing down, so this matrix turns clockwise.
    const dx = x - cx;
    const dy = y - cy;
    xs.push(canvas.width / 2 + dx * cos - dy * sin);
    ys.push(canvas.height / 2 + dx * sin + dy * cos);
  }
  return {
    x1: Math.min(...xs),
    y1: Math.min(...ys),
    x2: Math.max(...xs),
    y2: Math.max(...ys),
    width: canvas.width,
    height: canvas.height,
  };
}
