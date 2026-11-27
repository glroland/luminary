// Moving pictures: videos (played with <video>) and animated GIF/WebP/APNG
// (still an <img>). See docs/video-support.md.

export function isVideo(photo) {
  return photo?.media_kind === "video";
}

// Both kinds get the video-camera badge on their gallery tile.
export function isMoving(photo) {
  return photo?.media_kind === "video" || photo?.media_kind === "animated";
}

// 42 -> "0:42", 3723 -> "1:02:03"; null for an unknown length.
export function formatDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return null;
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}
