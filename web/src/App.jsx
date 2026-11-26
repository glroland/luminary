import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AboutDialog from "./components/AboutDialog.jsx";
import ChatWidget from "./components/ChatWidget.jsx";
import Logo from "./components/Logo.jsx";
import ManagementPage from "./components/ManagementPage.jsx";
import PhotoGrid from "./components/PhotoGrid.jsx";
import PhotoModal from "./components/PhotoModal.jsx";
import Sidebar from "./components/Sidebar.jsx";
import SortControl, { SORT_OPTIONS } from "./components/SortControl.jsx";
import {
  fetchIndividualFaces,
  fetchIndividuals,
  fetchPhotos,
  fetchJobs,
  fetchTagPhotoIds,
  fetchTags,
  startSync,
} from "./api/client.js";
import { applyTheme, getStoredTheme } from "./theme.js";

import { matchesLocation } from "./locations.js";

const POLL_INTERVAL_MS = 1000;
// Placeholder tiles shown while the photo list is first loading.
const SKELETON_TILE_COUNT = 24;

// A photo's directory is everything in its (root-relative) filename
// before the last "/" -- "" for one directly under the photos root.
function directoryOf(filename) {
  const slash = filename.lastIndexOf("/");
  return slash === -1 ? "" : filename.slice(0, slash);
}

// A photo's full date key, "YYYY-MM-DD", derived from created_at -- the
// same zero-padded shape Sidebar.jsx's buildDateTree groups photos into
// (year -> month -> day). Null for a missing/unparseable created_at (such
// a photo never matches a date filter, same as how one with no filename
// directory can still match the "" root of the folder tree, but there's
// no equivalent root bucket for dates since every photo either has one or
// doesn't). Because every segment is fixed-width and "-"-separated, a
// plain startsWith against a shorter selectedDate (just the year, or
// year-month) can't accidentally match a different year/month -- the
// separator always lands on a boundary.
function dateKeyOf(createdAt) {
  if (!createdAt) return null;
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return null;
  const year = String(date.getFullYear());
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

// A photo's date as a number for sorting, or null when it has none (the
// API sends created_at: null for an undated photo -- `new Date(null)`
// would silently be 1970).
function timeOf(photo) {
  if (!photo.created_at) return null;
  const time = new Date(photo.created_at).getTime();
  return Number.isNaN(time) ? null : time;
}

// Undated photos go last in either direction, so they stay one run (the
// grid's "Undated" section) at the end.
function compareDates(a, b, direction) {
  const timeA = timeOf(a);
  const timeB = timeOf(b);
  if (timeA === null || timeB === null) return (timeA === null) - (timeB === null);
  return (timeA - timeB) * direction;
}

// Comparator backing SortControl.jsx's options. Folder sort breaks ties by
// filename (rather than leaving same-folder photos in whatever order they
// arrived in) so the result is still deterministic within a folder.
function comparePhotos(a, b, sortOrder) {
  switch (sortOrder) {
    case "date-asc":
      return compareDates(a, b, 1);
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
      return compareDates(a, b, -1);
  }
}

export default function App() {
  const [photos, setPhotos] = useState([]);
  const [selectedPhoto, setSelectedPhoto] = useState(null);
  // Boxes the floating chat's object search located in selectedPhoto
  // for its latest turn; PhotoModal draws them. PhotoChat clears them
  // itself whenever the open photo changes.
  const [chatBoxes, setChatBoxes] = useState([]);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  // The server's one shared snapshot of every background job (see
  // GET /api/jobs) -- null until first fetched.
  const [jobs, setJobs] = useState(null);
  const previousJobsActiveRef = useRef(null);
  const [view, setView] = useState("gallery"); // "gallery" | "manage"
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // null means no filter (all photos); otherwise a directory path as it
  // appears in photo.filename, matched by directoryOf() below including
  // its subdirectories. "" is distinct from null -- it's the sidebar's
  // fixed "(Root)" entry, which narrows to photos with no directory
  // component at all (directoryOf() === ""), since those have no segment
  // of their own to select from the folder tree otherwise. Mutually
  // exclusive with selectedIndividual/searchResults below -- the
  // sidebar's parent sections (see Sidebar.jsx) are alternative ways to
  // narrow the same grid, not combinable filters.
  const [selectedDirectory, setSelectedDirectory] = useState(null);
  // null means no date filter; otherwise a dateKeyOf()-shaped string at
  // whichever granularity was clicked in the sidebar's "By Date" tree --
  // "2024" (year), "2024-05" (year-month), or "2024-05-14" (a single day).
  // Mutually exclusive with the other filters above, same convention.
  const [selectedDate, setSelectedDate] = useState(null);
  // null means no location filter; otherwise a locations.js path key (or
  // LOCATION_UNKNOWN) chosen in the sidebar's "By Location" tree.
  const [selectedLocation, setSelectedLocation] = useState(null);
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
  // The sidebar's "By Tag" section: the tag list (with photo counts), the
  // selected tag id (null = no tag filter), and that tag's photo ids --
  // same shape as selectedIndividual/individualPhotoIds above. tagsVersion
  // is bumped by every loadTags() so the selected tag's ids are re-fetched
  // too (a photo may have been tagged/untagged in the modal or the
  // management page since they were last fetched).
  const [tags, setTags] = useState([]);
  const [selectedTag, setSelectedTag] = useState(null);
  const [tagPhotoIds, setTagPhotoIds] = useState(null);
  const [tagsVersion, setTagsVersion] = useState(0);
  // Search is done by asking the chat (ChatWidget.jsx): the model runs a
  // search tool server-side and the reply carries its search list -- the
  // matching photo ids, each with a match score (handleSearchResults
  // below). searchResults is what narrows the grid --
  // null means the search filter isn't selected (same "null = no filter"
  // convention as selectedDirectory/selectedIndividual above), an array
  // (possibly empty, meaning "no matches") means it is. Kept in the API's
  // own best-score-first order -- never re-sorted here. lastSearchResults
  // remembers the most recent search (null before the first one) so the
  // sidebar's "Search Results" row can bring it back after browsing away.
  const [searchResults, setSearchResults] = useState(null);
  const [lastSearchResults, setLastSearchResults] = useState(null);
  // Bumped by resetGrid() whenever the filter/sort/search changes, and
  // used as PhotoGrid's `key` -- remounting it is what sends its
  // infinite-scroll window (see PhotoGrid.jsx) back to the first batch.
  // Deliberately *not* bumped when the photo list is merely reloaded
  // (after a sync), so that doesn't throw away how far someone has
  // scrolled.
  const [gridGeneration, setGridGeneration] = useState(0);
  // Viewport position of the tile that was clicked to open selectedPhoto,
  // which PhotoModal's open animation scales up from; null when the photo
  // was opened some other way (ManagementPage's "go to photo").
  const [modalOrigin, setModalOrigin] = useState(null);
  const [theme, setTheme] = useState(getStoredTheme);
  const [aboutOpen, setAboutOpen] = useState(false);
  // Gallery sort order (date/filename/folder, see SortControl.jsx). Not
  // applied while a search is active -- see visiblePhotos below.
  const [sortOrder, setSortOrder] = useState(SORT_OPTIONS[0].value);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // A new filter/sort is a new list: start it from the top, both the
  // rendered window and the scroll position (otherwise the page stays
  // scrolled deep into a list that now only has its first batch rendered).
  function resetGrid() {
    setGridGeneration((prev) => prev + 1);
    if (window.scrollY > 0) window.scrollTo(0, 0);
  }

  function handleSelectPhoto(photo, origin) {
    setModalOrigin(origin ?? null);
    setSelectedPhoto(photo);
  }

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

  const loadTags = useCallback(() => {
    fetchTags()
      .then((list) => {
        setTags(list);
        // A selected tag deleted since (on the management page) can't
        // filter anything any more -- drop the filter rather than 404.
        setSelectedTag((prev) => (prev !== null && !list.some((tag) => tag.id === prev) ? null : prev));
        setTagsVersion((prev) => prev + 1);
      })
      .catch((err) => setError(err.message));
  }, []);

  const refreshJobs = useCallback(
    (known) => {
      if (known) {
        setJobs(known);
        return Promise.resolve();
      }
      return fetchJobs()
        .then(setJobs)
        .catch(() => {});
    },
    []
  );

  useEffect(() => {
    loadPhotos();
    loadIndividuals();
    loadTags();
    refreshJobs();
  }, [loadPhotos, loadIndividuals, loadTags, refreshJobs]);

  // Keep the job snapshot fresh while it can matter: any job running (it
  // may be someone else's, and ending one reloads the gallery), or the
  // Manage page open (where anyone can start or watch one).
  const jobActive = Boolean(jobs?.active);
  useEffect(() => {
    if (!jobActive && view !== "manage") return undefined;
    const timer = setInterval(refreshJobs, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [jobActive, view, refreshJobs]);

  // A sync finishing -- started here or in another browser -- changes the
  // photo list.
  useEffect(() => {
    const previous = previousJobsActiveRef.current;
    previousJobsActiveRef.current = jobs?.active ?? null;
    if (previous === "sync" && jobs?.active !== "sync" && jobs?.sync?.status === "completed") {
      loadPhotos();
    }
  }, [jobs, loadPhotos]);

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

  // Same as the individual effect above, for the selected tag.
  useEffect(() => {
    if (selectedTag === null) {
      setTagPhotoIds(null);
      return;
    }
    let cancelled = false;
    fetchTagPhotoIds(selectedTag)
      .then((ids) => {
        if (!cancelled) setTagPhotoIds(new Set(ids));
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTag, tagsVersion]);

  async function handleRefresh() {
    try {
      // POST /api/sync takes the server's single job slot before it
      // responds (409 if any job is already running).
      await startSync();
    } catch (err) {
      setError(err.message);
    }
    refreshJobs();
  }

  // Used by ManagementPage's per-face "go to the photo this was tagged
  // from" -- looks the photo up in the already-loaded list rather than
  // fetching it again, since the full list is already in memory here.
  function handleNavigateToPhoto(photoId) {
    const photo = photos.find((candidate) => candidate.id === photoId);
    if (!photo) return;
    setView("gallery");
    handleSelectPhoto(photo);
  }

  // Shows `results` as the gallery's "Search Results": every other filter
  // is cleared (same "selecting one filter clears the others" rule the
  // sidebar sections follow) and any open photo is closed, since the
  // results are what the user asked to see.
  function showSearchResults(results) {
    setSearchResults(results);
    setSelectedDirectory(null);
    setSelectedIndividual(null);
    setSelectedTag(null);
    setSelectedDate(null);
    setSelectedLocation(null);
    setSelectedPhoto(null);
    setView("gallery");
    resetGrid();
  }

  // The chat changed its search list: `entries` are {id, filename, score}
  // (score 0-1), best score first. They're looked up in the already-loaded
  // list, like handleNavigateToPhoto above; an id not in it (indexed since
  // the list was loaded) is skipped. Each result is a copy of the photo
  // carrying its `search_score`, which PhotoCard shows on the tile.
  //
  // If any id is missing the loaded list is stale (photos re-indexed with new
  // ids, a sync from another tab, ...): reload it once and look again, rather
  // than silently showing fewer photos than the chat just announced.
  async function handleSearchResults(entries) {
    let loaded = photos;
    if (entries.some((entry) => !loaded.some((photo) => photo.id === entry.id))) {
      try {
        loaded = await fetchPhotos();
        setPhotos(loaded);
      } catch {
        // Keep what we have; the lookup below skips what it can't find.
      }
    }
    const byId = new Map(loaded.map((photo) => [photo.id, photo]));
    const results = entries
      .filter((entry) => byId.has(entry.id))
      .map((entry) => ({ ...byId.get(entry.id), search_score: entry.score }));
    if (results.length < entries.length) {
      console.warn(
        `Search returned ${entries.length} photos but only ${results.length} are in the photo list`,
      );
    }
    setLastSearchResults(results);
    showSearchResults(results);
  }

  // The same list in the shape the chat API takes it back in: sent with
  // every chat message so the model's tools can filter or truncate the
  // last search (the list itself never goes through the model's context).
  const chatSearchList = useMemo(
    () =>
      lastSearchResults === null
        ? null
        : lastSearchResults.map((photo) => ({
            id: photo.id,
            filename: photo.filename,
            score: photo.search_score,
          })),
    [lastSearchResults]
  );

  // Memoised so the array keeps its identity across unrelated re-renders
  // (sync-status polling, the modal opening) -- PhotoGrid's own memoised
  // section grouping depends on that. Search results keep the order the
  // search returned them in -- never re-sorted here (see SortControl.jsx's
  // comment).
  const visiblePhotos = useMemo(() => {
    if (searchResults !== null) return searchResults;
    const filtered =
      selectedIndividual !== null
        ? photos.filter((photo) => individualPhotoIds?.has(photo.id))
        : selectedTag !== null
          ? photos.filter((photo) => tagPhotoIds?.has(photo.id))
          : selectedDirectory !== null
          ? photos.filter((photo) => {
              const dir = directoryOf(photo.filename);
              return dir === selectedDirectory || dir.startsWith(`${selectedDirectory}/`);
            })
          : selectedLocation !== null
            ? photos.filter((photo) => matchesLocation(photo, selectedLocation))
          : selectedDate !== null
            ? photos.filter((photo) => {
                const key = dateKeyOf(photo.created_at);
                return key !== null && (key === selectedDate || key.startsWith(`${selectedDate}-`));
              })
            : [...photos];
    return filtered.sort((a, b) => comparePhotos(a, b, sortOrder));
  }, [
    photos,
    searchResults,
    selectedIndividual,
    individualPhotoIds,
    selectedTag,
    tagPhotoIds,
    selectedDirectory,
    selectedLocation,
    selectedDate,
    sortOrder,
  ]);

  // Section headers in the grid follow the sort: by month for the date
  // sorts, by folder for the folder sorts, none for filename sorts or
  // search results (relevance order) -- see PhotoGrid.jsx's `groupBy`.
  const groupBy =
    searchResults !== null
      ? null
      : sortOrder.startsWith("date")
        ? "month"
        : sortOrder.startsWith("folder")
          ? "folder"
          : null;

  // Shared by the header's hamburger and the copy PhotoGrid shows in its
  // stuck section header. Stable identity: it's a PhotoGrid effect dep.
  const handleToggleSidebar = useCallback(() => setSidebarOpen((prev) => !prev), []);

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
          setSelectedTag(null);
          setSelectedDate(null);
          setSelectedLocation(null);
          setSearchResults(null);
          setView("gallery");
          resetGrid();
        }}
        individuals={individuals}
        selectedIndividual={selectedIndividual}
        onSelectIndividual={(personId) => {
          setSelectedIndividual(personId);
          setSelectedTag(null);
          setSelectedDirectory(null);
          setSelectedDate(null);
          setSelectedLocation(null);
          setSearchResults(null);
          setView("gallery");
          resetGrid();
        }}
        tags={tags}
        selectedTag={selectedTag}
        onSelectTag={(tagId) => {
          setSelectedTag(tagId);
          setSelectedDirectory(null);
          setSelectedIndividual(null);
          setSelectedDate(null);
          setSelectedLocation(null);
          setSearchResults(null);
          setView("gallery");
          resetGrid();
        }}
        selectedLocation={selectedLocation}
        onSelectLocation={(locationKey) => {
          setSelectedLocation(locationKey);
          setSelectedDirectory(null);
          setSelectedIndividual(null);
          setSelectedTag(null);
          setSelectedDate(null);
          setSearchResults(null);
          setView("gallery");
          resetGrid();
        }}
        selectedDate={selectedDate}
        onSelectDate={(dateKey) => {
          setSelectedDate(dateKey);
          setSelectedLocation(null);
          setSelectedDirectory(null);
          setSelectedIndividual(null);
          setSelectedTag(null);
          setSearchResults(null);
          setView("gallery");
          resetGrid();
        }}
        onAllImages={() => {
          setSelectedDirectory(null);
          setSelectedIndividual(null);
          setSelectedTag(null);
          setSelectedDate(null);
          setSelectedLocation(null);
          setSearchResults(null);
          setView("gallery");
          resetGrid();
        }}
        onManage={() => setView("manage")}
        onClearSearch={() => {
          // Forget the search entirely; if its results are showing, go
          // back to the full gallery.
          const wasShowing = searchResults !== null;
          setLastSearchResults(null);
          setSearchResults(null);
          if (wasShowing) resetGrid();
        }}
        searchResultCount={lastSearchResults === null ? null : lastSearchResults.length}
        searchActive={searchResults !== null}
        onSearchResults={() => showSearchResults(lastSearchResults ?? [])}
      />
      <div className="app-main">
        <header className="app-header">
          <button
            type="button"
            className="sidebar-toggle"
            onClick={handleToggleSidebar}
            aria-label="Toggle sidebar"
            aria-expanded={sidebarOpen}
          >
            <span className="sidebar-toggle-icon" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </button>
          <Logo />
          {/* Right-aligned header controls (margin-left: auto pushes this
              group against the header's right edge, after the logo). The
              photo count and sort only apply to the gallery view once
              photos are loaded; the theme toggle is always there. */}
          <div className="app-header-controls">
            {view === "gallery" && !isLoading && (
              <>
                {searchResults !== null && (
                  <button
                    type="button"
                    className="reset-search-button"
                    onClick={() => {
                      setSearchResults(null);
                      resetGrid();
                    }}
                  >
                    Reset
                  </button>
                )}
                {visiblePhotos.length > 0 && (
                  <span className="photo-count">
                    {visiblePhotos.length.toLocaleString()} {visiblePhotos.length === 1 ? "photo" : "photos"}
                  </span>
                )}
                {searchResults === null && (
                  <SortControl
                    value={sortOrder}
                    onChange={(value) => {
                      setSortOrder(value);
                      resetGrid();
                    }}
                  />
                )}
              </>
            )}
            <button
              type="button"
              className="theme-toggle"
              onClick={() => setTheme((prev) => (prev === "dark" ? "light" : "dark"))}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            >
              <span aria-hidden="true">{theme === "dark" ? "\u2600" : "\u263E"}</span>
            </button>
            <button
              type="button"
              className="theme-toggle about-button"
              onClick={() => setAboutOpen(true)}
              aria-label="About Luminary"
              title="About Luminary"
            >
              <span aria-hidden="true">i</span>
            </button>
          </div>
        </header>
        {aboutOpen && <AboutDialog onClose={() => setAboutOpen(false)} />}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {view === "manage" ? (
          <ManagementPage
            jobs={jobs}
            onJobsChange={refreshJobs}
            onRefresh={handleRefresh}
            onBack={() => {
              setView("gallery");
              // The individuals catalog may have changed (added/renamed/
              // deleted) while on this page -- refresh the sidebar's copy.
              loadIndividuals();
              loadTags();
            }}
            onNavigateToPhoto={handleNavigateToPhoto}
            onPurged={() => {
              setSelectedDirectory(null);
              setSelectedIndividual(null);
              setSelectedTag(null);
              setSelectedDate(null);
              setSelectedLocation(null);
              setSearchResults(null);
              setLastSearchResults(null);
              setIndividuals([]);
              setTags([]);
              setSelectedPhoto(null);
              resetGrid();
              loadPhotos();
            }}
          />
        ) : isLoading ? (
          <div className="photo-grid" aria-label="Loading photos" aria-busy="true">
            {Array.from({ length: SKELETON_TILE_COUNT }, (_, index) => (
              <div key={index} className="photo-skeleton" />
            ))}
          </div>
        ) : (
          <PhotoGrid
            key={gridGeneration}
            photos={visiblePhotos}
            groupBy={groupBy}
            onToggleSidebar={handleToggleSidebar}
            sidebarOpen={sidebarOpen}
            keysEnabled={!selectedPhoto}
            onSelect={handleSelectPhoto}
            emptyMessage={
              searchResults === null
                ? "No photos yet."
                : lastSearchResults === null
                  ? "No search yet. Ask the chat to find photos."
                  : "No photos match your search."
            }
          />
        )}
        {/* `photos` here is the list the arrow keys/filmstrip step through:
            whatever the gallery is currently showing, in its order. */}
        <PhotoModal
          photo={selectedPhoto}
          photos={visiblePhotos}
          origin={modalOrigin}
          chatBoxes={chatBoxes}
          onNavigate={setSelectedPhoto}
          onClose={() => {
            setSelectedPhoto(null);
            // Tags may have been added/removed on this photo in the modal.
            loadTags();
          }}
        />
      </div>
      {/* The chat sees whatever photo is open in the modal, and is also
          how the library is searched. */}
      <ChatWidget
        photo={selectedPhoto}
        searchList={chatSearchList}
        onBoxesChange={setChatBoxes}
        onSearchResults={handleSearchResults}
      />
    </div>
  );
}
