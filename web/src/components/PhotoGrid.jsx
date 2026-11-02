import PhotoCard from "./PhotoCard.jsx";

export default function PhotoGrid({ photos, onSelect, emptyMessage = "No photos yet." }) {
  if (photos.length === 0) {
    return <p className="empty-state">{emptyMessage}</p>;
  }

  return (
    <div className="photo-grid">
      {photos.map((photo) => (
        <PhotoCard key={photo.id} photo={photo} onSelect={onSelect} />
      ))}
    </div>
  );
}
