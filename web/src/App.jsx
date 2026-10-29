import { useCallback, useEffect, useRef, useState } from "react";
import PhotoGrid from "./components/PhotoGrid.jsx";
import PhotoModal from "./components/PhotoModal.jsx";
import SyncStatus from "./components/SyncStatus.jsx";
import { fetchPhotos, fetchSyncStatus, startSync } from "./api/client.js";
import logo from "./assets/logo.svg";

const POLL_INTERVAL_MS = 1000;
const DONE_DISPLAY_MS = 2000;

export default function App() {
  const [photos, setPhotos] = useState([]);
  const [selectedPhoto, setSelectedPhoto] = useState(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [syncStatus, setSyncStatus] = useState(null);
  const pollRef = useRef(null);

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

  const pollSyncStatus = useCallback(() => {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const status = await fetchSyncStatus();
        if (status.status === "running") {
          setSyncStatus(status);
          return;
        }
        clearInterval(pollRef.current);
        if (status.status === "completed") {
          setSyncStatus(status);
          loadPhotos();
        } else if (status.status === "error") {
          setSyncStatus(status);
        }
        setTimeout(() => setSyncStatus(null), DONE_DISPLAY_MS);
      } catch {
        clearInterval(pollRef.current);
        setSyncStatus(null);
      }
    }, POLL_INTERVAL_MS);
  }, [loadPhotos]);

  useEffect(() => {
    loadPhotos();
    fetchSyncStatus()
      .then((status) => {
        if (status.status === "running") {
          setSyncStatus(status);
          pollSyncStatus();
        }
      })
      .catch(() => {});
    return () => clearInterval(pollRef.current);
  }, [loadPhotos, pollSyncStatus]);

  async function handleRefresh() {
    try {
      // POST /api/sync flips status to "running" before it responds, so we
      // could just use its response directly -- setting this locally too
      // just means the progress UI appears one round-trip sooner.
      await startSync();
      setSyncStatus({ status: "running", processed: 0, total: 0 });
      pollSyncStatus();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <img className="app-logo" src={logo} alt="Luminary" />
        <SyncStatus status={syncStatus} onRefresh={handleRefresh} />
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>Loading photos...</p>
      ) : (
        <PhotoGrid photos={photos} onSelect={setSelectedPhoto} />
      )}
      <PhotoModal photo={selectedPhoto} onClose={() => setSelectedPhoto(null)} />
    </div>
  );
}
