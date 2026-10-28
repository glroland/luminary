import { useCallback, useEffect, useState } from "react";
import PhotoGrid from "./components/PhotoGrid.jsx";
import PhotoModal from "./components/PhotoModal.jsx";
import UploadButton from "./components/UploadButton.jsx";
import { deletePhoto, fetchPhotos } from "./api/client.js";

export default function App() {
  const [photos, setPhotos] = useState([]);
  const [selectedPhoto, setSelectedPhoto] = useState(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  const loadPhotos = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await fetchPhotos();
      setPhotos(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPhotos();
  }, [loadPhotos]);

  function handleUploaded(photo) {
    setPhotos((current) => [photo, ...current]);
  }

  async function handleDelete(photoId) {
    try {
      await deletePhoto(photoId);
      setPhotos((current) => current.filter((photo) => photo.id !== photoId));
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Luminary</h1>
        <UploadButton onUploaded={handleUploaded} />
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>Loading photos...</p>
      ) : (
        <PhotoGrid photos={photos} onSelect={setSelectedPhoto} onDelete={handleDelete} />
      )}
      <PhotoModal photo={selectedPhoto} onClose={() => setSelectedPhoto(null)} />
    </div>
  );
}
