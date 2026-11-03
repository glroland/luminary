import { useCallback, useEffect, useRef, useState } from "react";
import ManagementPage from "./components/ManagementPage.jsx";
import { PageSizeSelect, PaginationNav } from "./components/Pagination.jsx";
import PhotoGrid from "./components/PhotoGrid.jsx";
import PhotoModal from "./components/PhotoModal.jsx";
import Sidebar, { SEARCH_THRESHOLD_OPTIONS } from "./components/Sidebar.jsx";
import SortControl, { SORT_OPTIONS } from "./components/SortControl.jsx";
import {
  fetchIndividualFaces,
  fetchIndividuals,
  fetchPhotos,
  fetchSyncStatus,
  searchPhotos,
  startSync,
} from "./api/client.js";
import logo from "./assets/logo.svg";

const POLL_INTERVAL_MS = 1000;
const DONE_DISPLAY_MS = 2000;
const PAGE_SIZE_OPTIONS = [24, 48, 96, 200];

// A photo's directory is everything in its (root-relative) filename
// before the last "/" -- "" for one directly under the photos root.
function directoryOf(filename) {
  const slash = filename.lastIndexOf("/");
  return slash === -1 ? "" : filename.slice(0, slash);
}

// Comparator backing SortControl.jsx's options. Folder sort breaks ties by
// filename (rather than leaving same-folder photos in whatever order they
// arrived in) so the result is still deterministic within a folder.
function comparePhotos(a, b, sortOrder) {
  switch (sortOrder) {
    case "date-asc":
      return new Date(a.created_at) - new Date(b.created_at);
    case "filename-asc":
      return a.filename.localeCompare(b.filename);
    case "filename-desc":
      return b.filename.localeCompare(a.filename);
    case "folder-asc":
      return (
        directoryOf(a.filename).localeCompare(directoryOf(b.filename)) ||
        a.filename.localeCompare(b.filename)
      );
    case "folder-desc":
      return (
        directoryOf(b.filename).localeCompare(directoryOf(a.filename)) ||
        a.filename.localeCompare(b.filename)
      );
    case "date-desc":
    default:
      return new Date(b.created_at) - new Date(a.created_at);
  }
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
  // its subdirectories. Mutually exclusive with selectedIndividual/
  // searchResults below -- the sidebar's four parent sections (see
  // Sidebar.jsx) are alternative ways to narrow the same grid, not
  // combinable filters.
  const [selectedDirectory, setSelectedDirectory] = useState(null);
  // The catalogued-individuals list for the sidebar's "Individuals"
  // section; ManagementPage owns the full CRUD lifecycle for these, this
  // is just a read-only copy kept here so Sidebar can render it without
  // every page needing its own fetch.
  const [individuals, setIndividuals] = useState([]);
  // null means no individual filter; otherwise a person id, matched against
  // individualPhotoIds below (that person's tagged photos, fetched
  // whenever the selection changes).
  const [selectedIndividual, setSelectedIndividual] = useState(null);
  const [individualPhotoIds, setIndividualPhotoIds] = useState(null);
  // The sidebar's "Search" section (free-text similarity search over
  // photos.description, see enrich.py/embeddings.py). searchQuery/
  // searchThreshold are just the controlled form fields; searchResults is
  // what actually narrows the grid -- null means no search is active
  // (same "null = no filter" convention as selectedDirectory/
  // selectedIndividual above), an array (possibly empty, meaning "no
  // matches") means one is. Already sorted most-similar-first by the API
  // itself (GET /api/photos/search) -- never re-sorted here.
  const [searchQuery, setSearchQuery] = useState("");
  const [searchThreshold, setSearchThreshold] = useState(SEARCH_THRESHOLD_OPTIONS[1].value);
  const [searchResults, setSearchResults] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState(null);
  // Pagination over visiblePhotos below (client-side -- the full list is
  // already loaded into `photos`, same as the directory/individual
  // filters). `page` is 1-based and clamped against the current
  // pageSize/filter combination at render time rather than via an effect,
  // so switching filters or page size never briefly shows a stale,
  // out-of-range page.
  const [pageSize, setPageSize] = useState(PAGE_SIZE_OPTIONS[0]);
  const [page, setPage] = useState(1);
  // Gallery sort order (date/filename/folder, see SortControl.jsx). Not
  // applied while a search is active -- see visiblePhotos below.
  const [sortOrder, setSortOrder] = useState(SORT_OPTIONS[0].value);
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

  const loadIndividuals = useCallback(() => {
    fetchIndividuals()
      .then(setIndividuals)
      .catch((err) => setError(err.message));
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
    loadIndividuals();
    fetchSyncStatus()
      .then((status) => {
        if (status.status === "running") {
          setSyncStatus(status);
          pollSyncStatus();
        }
      })
      .catch(() => {});
    return () => clearInterval(pollRef.current);
  }, [loadPhotos, loadIndividuals, pollSyncStatus]);

  // Fetches the selected individual's tagged photo ids whenever the
  // selection changes -- the faces endpoint, not a bulk photos-by-person
  // one, since that's what the API already exposes (ManagementPage's
  // accordions use the same call for the same reason).
  useEffect(() => {
    if (selectedIndividual === null) {
      setIndividualPhotoIds(null);
      return;
    }
    let cancelled = false;
    fetchIndividualFaces(selectedIndividual)
      .then((faces) => {
        if (!cancelled) setIndividualPhotoIds(new Set(faces.map((face) => face.photo_id)));
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedIndividual]);

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

  async function handleSearch() {
    const query = searchQuery.trim();
    if (!query) return;
    setSearchLoading(true);
    setSearchError(null);
    try {
      const results = await searchPhotos(query, searchThreshold);
      setSearchResults(results);
      // Same "selecting one filter clears the others" rule the other
      // three sidebar sections already follow (see onSelectDirectory/
      // onSelectIndividual/onAllImages below).
      setSelectedDirectory(null);
      setSelectedIndividual(null);
      setView("gallery");
      setPage(1);
    } catch (err) {
      setSearchError(err.message);
    } finally {
      setSearchLoading(false);
    }
  }

  // Cancels an active search (the sidebar's small "x") -- resets the form
  // fields too, not just the results, so re-opening "Search" starts clean
  // rather than showing a stale query for a filter that's no longer applied.
  function handleClearSearch() {
    setSearchResults(null);
    setSearchQuery("");
    setSearchThreshold(SEARCH_THRESHOLD_OPTIONS[1].value);
    setSearchError(null);
  }

  const filteredPhotos =
    selectedIndividual !== null
      ? photos.filter((photo) => individualPhotoIds?.has(photo.id))
      : selectedDirectory === null
        ? photos
        : photos.filter((photo) => {
            const dir = directoryOf(photo.filename);
            return dir === selectedDirectory || dir.startsWith(`${selectedDirectory}/`);
          });

  // Search results are already ordered most-similar-first by the API --
  // never re-sorted here (see SortControl.jsx's comment).
  const visiblePhotos =
    searchResults !== null
      ? searchResults
      : [...filteredPhotos].sort((a, b) => comparePhotos(a, b, sortOrder));

  const totalPages = Math.max(1, Math.ceil(visiblePhotos.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const paginatedPhotos = visiblePhotos.slice((currentPage - 1) * pageSize, currentPage * pageSize);

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
          setSelectedIndividual(null);
          setSearchResults(null);
          setView("gallery");
          setPage(1);
        }}
        individuals={individuals}
        selectedIndividual={selectedIndividual}
        onSelectIndividual={(personId) => {
          setSelectedIndividual(personId);
          setSelectedDirectory(null);
          setSearchResults(null);
          setView("gallery");
          setPage(1);
        }}
        onAllImages={() => {
          setSelectedDirectory(null);
          setSelectedIndividual(null);
          setSearchResults(null);
          setView("gallery");
          setPage(1);
        }}
        onManage={() => setView("manage")}
        searchQuery={searchQuery}
        onSearchQueryChange={setSearchQuery}
        searchThreshold={searchThreshold}
        onSearchThresholdChange={setSearchThreshold}
        onSearch={handleSearch}
        onClearSearch={handleClearSearch}
        searchActive={searchResults !== null}
        searchLoading={searchLoading}
        searchError={searchError}
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
          {/* Right-aligned header controls (margin-left: auto pushes this
              group against the header's right edge, after the logo) --
              only meaningful for the gallery view once photos are loaded,
              same gating the toolbar/Pagination previously had below the
              grid. Left-to-right: sort, per-page, pagination nav. */}
          {view === "gallery" && !isLoading && (
            <div className="app-header-controls">
              {searchResults === null && (
                <SortControl
                  value={sortOrder}
                  onChange={(value) => {
                    setSortOrder(value);
                    setPage(1);
                  }}
                />
              )}
              {visiblePhotos.length > 0 && (
                <>
                  <PageSizeSelect
                    pageSize={pageSize}
                    pageSizeOptions={PAGE_SIZE_OPTIONS}
                    onPageSizeChange={(size) => {
                      setPageSize(size);
                      setPage(1);
                    }}
                  />
                  <PaginationNav page={currentPage} totalPages={totalPages} onPageChange={setPage} />
                </>
              )}
            </div>
          )}
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
            onBack={() => {
              setView("gallery");
              // The individuals catalog may have changed (added/renamed/
              // deleted) while on this page -- refresh the sidebar's copy.
              loadIndividuals();
            }}
            onNavigateToPhoto={handleNavigateToPhoto}
            onPurged={() => {
              setSelectedDirectory(null);
              setSelectedIndividual(null);
              setSearchResults(null);
              setIndividuals([]);
              setSelectedPhoto(null);
              setPage(1);
              loadPhotos();
            }}
          />
        ) : isLoading ? (
          <p>Loading photos...</p>
        ) : (
          <PhotoGrid
            photos={paginatedPhotos}
            onSelect={setSelectedPhoto}
            emptyMessage={searchResults !== null ? "No photos match your search." : "No photos yet."}
          />
        )}
        <PhotoModal photo={selectedPhoto} onClose={() => setSelectedPhoto(null)} />
      </div>
    </div>
  );
}
