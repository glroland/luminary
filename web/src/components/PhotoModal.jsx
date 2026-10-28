import { originalUrl } from "../api/client.js";

export default function PhotoModal({ photo, onClose }) {
  if (!photo) return null;

  return (
    <div className="photo-modal-backdrop" onClick={onClose}>
      <div className="photo-modal" onClick={(event) => event.stopPropagation()}>
        <img src={originalUrl(photo.id)} alt={photo.filename} />
        <div className="photo-modal-meta">
          <h2>{photo.filename}</h2>
          <p>
            {photo.width} &times; {photo.height}
          </p>
          <p>{new Date(photo.uploaded_at).toLocaleString()}</p>
        </div>
        <button type="button" className="close-button" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
