import { originalUrl } from "../api/client.js";
import PhotoChat from "./PhotoChat.jsx";
import PhotoMap from "./PhotoMap.jsx";

export default function PhotoModal({ photo, onClose }) {
  if (!photo) return null;

  return (
    <div className="photo-modal-backdrop" onClick={onClose}>
      <div className="photo-modal" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="close-button" onClick={onClose}>
          Close
        </button>

        <div className="photo-modal-image-pane">
          <img src={originalUrl(photo.id)} alt={photo.filename} />
        </div>

        <div className="photo-modal-sidebar">
          <div className="photo-modal-meta">
            <h2>{photo.filename}</h2>
            <p>
              {photo.width} &times; {photo.height}
            </p>
            <p>{new Date(photo.created_at).toLocaleString()}</p>
          </div>

          <PhotoMap latitude={photo.latitude} longitude={photo.longitude} />

          <PhotoChat photoId={photo.id} />
        </div>
      </div>
    </div>
  );
}
