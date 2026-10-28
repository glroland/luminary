import { thumbnailUrl } from "../api/client.js";

export default function PhotoCard({ photo, onSelect, onDelete }) {
  return (
    <div className="photo-card">
      <img src={thumbnailUrl(photo.id)} alt={photo.filename} onClick={() => onSelect(photo)} />
      <div className="photo-card-footer">
        <span className="filename" title={photo.filename}>
          {photo.filename}
        </span>
        <button type="button" aria-label={`Delete ${photo.filename}`} onClick={() => onDelete(photo.id)}>
          Delete
        </button>
      </div>
    </div>
  );
}
