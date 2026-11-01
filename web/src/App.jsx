import { useCallback, useEffect, useRef, useState } from "react";
import ManagementPage from "./components/ManagementPage.jsx";
import PhotoGrid from "./components/PhotoGrid.jsx";
import PhotoModal from "./components/PhotoModal.jsx";
import Sidebar from "./components/Sidebar.jsx";
import { fetchPhotos, fetchSyncStatus, startSync } from "./api/client.js";
import logo from "./assets/logo.svg";

const POLL_INTERVAL_MS = 1000;
const DONE_DISPLAY_MS = 2000;

// A photo's directory is everything in its (root-relative) filename
// before the last "/" -- "" for one directly under the photos root.
function directoryOf(filename) {
  const slash = filename.lastIndexOf("/");
  return slash === -1 ? "" : filename.slice(0, slash);
}

export default function App() {
  const [photos, setPhotos] = useState([]);
  const [selectedPhoto, setSelectedPhoto] = useState(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [syncStatus, setSyncStatus] = useState(null);
  const [view, setView] = useState("gallery"); // "gallery" | "manage"
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // null means no filter (all photos); otherwise a directory path as it
  // appears in photo.filename, matched by directoryOf() below including
  // its subdirectories.
  const [selectedDirectory, setSelectedDirectory] = useState(null);
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

  // Used by ManagementPage's per-face "go to the photo this was tagged
  // from" -- looks the photo up in the already-loaded list rather than
  // fetching it again, since the full list is already in memory here.
  function handleNavigateToPhoto(photoId) {
    const photo = photos.find((candidate) => candidate.id === photoId);
    if (!photo) return;
    setView("gallery");
    setSelectedPhoto(photo);
  }

  const visiblePhotos =
    selectedDirectory === null
      ? photos
      : photos.filter((photo) => {
          const dir = directoryOf(photo.filename);
          return dir === selectedDirectory || dir.startsWith(`${selectedDirectory}/`);
        });

  return (
    <div className="app">
      {/* A traditional sidebar: it's a column in this flex layout, not a
          fixed overlay, so leaving it open never blocks the main view
          beside it (see Sidebar.jsx/styles.css's ".sidebar" rules). It's
          the first child here, flush against the window's left edge with
          no gap, regardless of open/closed state. */}
      <Sidebar
        open={sidebarOpen}
        photos={photos}
        selectedDirectory={selectedDirectory}
        onSelectDirectory={(directory) => {
          setSelectedDirectory(directory);
          setView("gallery");
        }}
        onManage={() => setView("manage")}
      />
      <div className="app-main">
        <header className="app-header">
          <button
            type="button"
            className="sidebar-toggle"
            onClick={() => setSidebarOpen((prev) => !prev)}
            aria-label="Toggle sidebar"
            aria-expanded={sidebarOpen}
          >
            <span className="sidebar-toggle-icon" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </button>
          <img className="app-logo" src={logo} alt="Luminary" />
        </header>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {view === "manage" ? (
          <ManagementPage
            syncStatus={syncStatus}
            onRefresh={handleRefresh}
            onBack={() => setView("gallery")}
            onNavigateToPhoto={handleNavigateToPhoto}
            onPurged={() => {
              setSelectedDirectory(null);
              setSelectedPhoto(null);
              loadPhotos();
            }}
          />
        ) : isLoading ? (
          <p>Loading photos...</p>
        ) : (
          <PhotoGrid photos={visiblePhotos} onSelect={setSelectedPhoto} />
        )}
        <PhotoModal photo={selectedPhoto} onClose={() => setSelectedPhoto(null)} />
      </div>
    </div>
  );
}
