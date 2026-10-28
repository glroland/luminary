import { useCallback, useEffect, useState } from "react";
import PhotoGrid from "./components/PhotoGrid.jsx";
import PhotoModal from "./components/PhotoModal.jsx";
import { deletePhoto, fetchPhotos } from "./api/client.js";
import logo from "./assets/logo.svg";

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
        <img className="app-logo" src={logo} alt="Luminary" />
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
