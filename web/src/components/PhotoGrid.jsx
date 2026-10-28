import PhotoCard from "./PhotoCard.jsx";

export default function PhotoGrid({ photos, onSelect, onDelete }) {
  if (photos.length === 0) {
    return <p className="empty-state">No photos yet. Upload your first one!</p>;
  }

  return (
    <div className="photo-grid">
      {photos.map((photo) => (
        <PhotoCard key={photo.id} photo={photo} onSelect={onSelect} onDelete={onDelete} />
      ))}
    </div>
  );
}
