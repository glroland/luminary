import { useEffect, useState } from "react";
import { originalUrl, thumbnailUrl } from "../api/client.js";

// Plays a video with the browser's own <video> element: the original is
// streamed as-is (the API honours Range, so seeking works) and never
// transcoded, so a file plays only if this browser can decode it -- H.264
// .mp4/.mov nearly everywhere, other codecs/containers depending on the
// browser. When it can't, the element fires `error` and this shows the poster
// with a download link instead. (`canPlayType` isn't consulted up front:
// Chrome answers "" for video/quicktime yet plays H.264 .mov fine.)
//
// This is the only place that knows how a video is played, so a WASM decoder
// could be dropped in here later as the fallback without touching the modal.
//
// `onReady` fires once the player has something to show (metadata loaded, or
// it failed), which is what lets PhotoModal stop polling the photos backend.
export default function VideoPlayer({ photo, onReady }) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [photo.id]);

  if (failed) {
    return (
      <div className="video-player video-player-unsupported" role="alert">
        <img src={thumbnailUrl(photo.id)} alt={photo.filename} />
        <div className="video-player-unsupported-message">
          <p>This browser can&rsquo;t play this video format.</p>
          <a href={originalUrl(photo.id)} download={photo.filename.split("/").pop()}>
            Download the original
          </a>
        </div>
      </div>
    );
  }

  return (
    <video
      className="video-player"
      controls
      playsInline
      preload="metadata"
      poster={thumbnailUrl(photo.id)}
      src={originalUrl(photo.id)}
      aria-label={photo.filename}
      onLoadedMetadata={() => onReady?.()}
      onError={() => {
        setFailed(true);
        onReady?.();
      }}
    />
  );
}
