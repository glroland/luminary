import { useMemo, useState } from "react";
import { individualThumbnailUrl } from "../api/client.js";
import DateTree from "./DateTree.jsx";
import DirectoryTree from "./DirectoryTree.jsx";

// A few reasonable cosine-similarity cutoffs for the search section's
// threshold dropdown below -- not verified-correct values (see
// config.get_face_match_threshold's own docstring for why this app
// generally treats similarity thresholds as starting points to tune
// against real data, not universal constants), just a sensible spread
// from loose to strict.
export const SEARCH_THRESHOLD_OPTIONS = [
  { value: 0.3, label: "Loose match (0.3)" },
  { value: 0.5, label: "Moderate match (0.5)" },
  { value: 0.7, label: "Strict match (0.7)" },
  { value: 0.85, label: "Very strict match (0.85)" },
];

// Builds a nested {children: {name: node}} tree of every directory that
// appears in `photos`' filenames (relative paths, e.g.
// "vacation/2019/beach.jpg") -- there's no dedicated directory concept in
// the API, just the photos' own paths, so the tree is derived here rather
// than fetched.
function buildTree(photos) {
  const root = { children: {}, count: 0, direct: 0 };
  for (const photo of photos) {
    const parts = photo.filename.split("/");
    parts.pop(); // the filename itself, not a directory
    let node = root;
    // `count` is every photo at or below a node -- the same set selecting
    // that folder filters the grid to (App.jsx matches subdirectories too).
    // root.direct is the "(Root)" entry's: photos with no folder at all.
    root.count += 1;
    if (parts.length === 0) root.direct += 1;
    for (const part of parts) {
      node.children[part] ??= { children: {}, count: 0 };
      node = node.children[part];
      node.count += 1;
    }
  }
  return root;
}

// Builds a nested {children: {key: node}} tree, year -> month -> day, from
// each photo's created_at -- same "derived from the photos themselves, not
// a dedicated API concept" reasoning as buildTree above. Keys are
// zero-padded strings ("05", not "5") so DateTree.jsx's lexical sort and
// App.jsx's "-"-joined dateKeyOf/startsWith filtering agree with each
// other; photos with no (or unparseable) created_at are left out of the
// tree entirely rather than grouped under some placeholder bucket.
function buildDateTree(photos) {
  const root = { children: {} };
  for (const photo of photos) {
    const date = new Date(photo.created_at);
    if (Number.isNaN(date.getTime())) continue;
    const year = String(date.getFullYear());
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    const yearNode = (root.children[year] ??= { children: {}, count: 0 });
    const monthNode = (yearNode.children[month] ??= { children: {}, count: 0 });
    const dayNode = (monthNode.children[day] ??= { children: {}, count: 0 });
    yearNode.count += 1;
    monthNode.count += 1;
    dayNode.count += 1;
  }
  return root;
}

// A collapsible parent section ("Folders", "Individuals") -- separate from
// each individual DirectoryTree node's own expand/collapse (also +/−
// style, see DirectoryTree.jsx), this just shows/hides the section's whole
// contents so a long individuals list doesn't dominate the sidebar.
// Small stroke icons for the section headers / "All Images" / "Manage".
// Drawn in currentColor so they follow the theme and each row's own
// hover/active colour; always aria-hidden, so a button's accessible name
// stays just its text.
const ICON_PATHS = {
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  person: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 20a8 8 0 0 1 16 0",
  tag: "M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9zM7.5 7.5h.01",
  calendar: "M5 6h14a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zM4 10h16M8 3v4M16 3v4",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
  sliders: "M4 7h10M18 7h2M4 17h2M10 17h10M16 4v6M8 14v6",
};

function Icon({ name }) {
  return (
    <svg
      className="sidebar-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

function Section({ title, icon, expanded, onToggle, children }) {
  return (
    <div className="sidebar-section">
      <button
        type="button"
        className="sidebar-section-header"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <Icon name={icon} />
        {title}
        {/* A chevron that styles.css rotates off the button's aria-expanded. */}
        <span className="sidebar-section-toggle" aria-hidden="true">
          ›
        </span>
      </button>
      {expanded && children}
    </div>
  );
}

// The "Search" section's text query + similarity-threshold dropdown, plus
// a small "x" to cancel an active search (only shown once one is active --
// there's nothing to cancel otherwise). A plain <form> so pressing Enter
// in the textarea submits it without needing its own keydown handling;
// textareas don't submit on Enter by default the way a single-line
// <input> does, which is exactly why there's also an explicit Search
// button, not just the dropdown/textarea.
function SearchSection({
  query,
  onQueryChange,
  threshold,
  onThresholdChange,
  onSearch,
  onClear,
  active,
  loading,
  error,
}) {
  return (
    <div className="sidebar-search">
      <form
        className="sidebar-search-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSearch();
        }}
      >
        <textarea
          className="sidebar-search-input"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search photo descriptions…"
          rows={3}
        />
        <select
          className="sidebar-search-threshold"
          value={threshold}
          onChange={(event) => onThresholdChange(Number(event.target.value))}
          aria-label="Similarity threshold"
        >
          {SEARCH_THRESHOLD_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <div className="sidebar-search-actions">
          <button type="submit" disabled={!query.trim() || loading}>
            {loading ? "Searching…" : "Search"}
          </button>
          {active && (
            <button
              type="button"
              className="sidebar-search-clear"
              onClick={onClear}
              aria-label="Clear search"
              title="Clear search"
            >
              ×
            </button>
          )}
        </div>
      </form>
      {error && (
        <p role="alert" className="error sidebar-search-error">
          {error}
        </p>
      )}
    </div>
  );
}

// A traditional, non-overlaying sidebar: it occupies its own column in the
// page's flex layout (see App.jsx/styles.css's ".app"/".sidebar" rules),
// flush against the window's left edge, rather than floating a
// fixed-position panel over the main content -- so it can be left open
// indefinitely without blocking interaction with the gallery/management
// views beside it. The hamburger toggle that opens/closes it lives in
// App.jsx's header, next to the logo, not here -- it has to stay visible
// and clickable regardless of open/closed state, which it couldn't do as
// part of this collapsing <aside>.
//
// "Search" sits apart from the other sections, pinned in the footer above
// the "Manage" button (see the JSX below).
//
// Five parent sections narrow the photo grid, mutually exclusive with each
// other (selecting one clears any other filter -- see App.jsx's
// handleSelectDirectory/handleSelectIndividual/handleSelectDate/
// handleAllImages/handleSearch): "All Images" clears every filter, "By
// Folder" filters by directory (via DirectoryTree, built from the photos'
// own paths), "By Individual" filters to photos containing a selected
// catalogued person, "By Tag" (below By Individual) filters to photos with
// a selected tag attached, "By Date" (below By Tag) filters by the
// photo's created_at via a year -> month -> day tree (DateTree, built from
// the photos' own created_at the same way the folder tree is built from
// their filenames -- there's no dedicated "date" concept in the API
// either), and "Search" narrows to photos whose description is similar
// enough to a free-text query (embeddings.py's similarity search), sorted
// most-similar first -- that ordering comes from the API response itself
// (GET /api/photos/search), not anything sorted client-side here.
export default function Sidebar({
  open,
  photos,
  selectedDirectory,
  onSelectDirectory,
  individuals,
  selectedIndividual,
  onSelectIndividual,
  tags = [],
  selectedTag = null,
  onSelectTag = () => {},
  selectedDate,
  onSelectDate,
  onAllImages,
  onManage,
  searchQuery = "",
  onSearchQueryChange = () => {},
  searchThreshold = SEARCH_THRESHOLD_OPTIONS[1].value,
  onSearchThresholdChange = () => {},
  onSearch = () => {},
  onClearSearch = () => {},
  searchActive = false,
  searchLoading = false,
  searchError = null,
}) {
  const tree = useMemo(() => buildTree(photos), [photos]);
  const dateTree = useMemo(() => buildDateTree(photos), [photos]);
  const [foldersExpanded, setFoldersExpanded] = useState(false);
  const [individualsExpanded, setIndividualsExpanded] = useState(false);
  const [tagsExpanded, setTagsExpanded] = useState(false);
  const [datesExpanded, setDatesExpanded] = useState(false);
  const [searchExpanded, setSearchExpanded] = useState(true);
  const noFilter =
    selectedDirectory === null &&
    selectedIndividual === null &&
    selectedTag === null &&
    selectedDate === null &&
    !searchActive;

  return (
    <aside className={`sidebar${open ? " sidebar-open" : ""}`}>
      <nav className="sidebar-tree">
        <button
          type="button"
          className={`sidebar-all-images${noFilter ? " sidebar-all-images-active" : ""}`}
          onClick={onAllImages}
          data-count={photos.length > 0 ? photos.length.toLocaleString() : undefined}
        >
          <Icon name="grid" />
          All Images
        </button>

        <Section title="By Folder" icon="folder" expanded={foldersExpanded} onToggle={() => setFoldersExpanded((prev) => !prev)}>
          {/* A photo placed directly under the photos root (no "/" in its
              filename) has no segment to appear as in buildTree's nested
              map, so it would otherwise have no way to be selected from
              this tree at all -- unlike every subfolder, which always gets
              its own node. This fixed entry (selectedDirectory === "",
              distinct from null's "no filter") is the only way to narrow
              the grid to just those root-level photos, and it's always
              shown, even when the tree below is empty. */}
          <ul className="directory-tree">
            <li>
              <div
                className={`directory-tree-item${selectedDirectory === "" ? " directory-tree-item-active" : ""}`}
                data-count={tree.direct > 0 ? tree.direct.toLocaleString() : undefined}
              >
                <span className="directory-tree-toggle-spacer" aria-hidden="true" />
                <button type="button" className="directory-tree-label" onClick={() => onSelectDirectory("")}>
                  (Root)
                </button>
              </div>
            </li>
          </ul>
          <DirectoryTree
            node={tree}
            path={null}
            selectedDirectory={selectedDirectory}
            onSelectDirectory={onSelectDirectory}
          />
        </Section>

        <Section
          title="By Individual"
          icon="person"
          expanded={individualsExpanded}
          onToggle={() => setIndividualsExpanded((prev) => !prev)}
        >
          {individuals.length === 0 ? (
            <p className="sidebar-individuals-empty">No individuals catalogued yet.</p>
          ) : (
            <ul className="sidebar-individuals">
              {individuals.map((individual) => (
                <li key={individual.id}>
                  <button
                    type="button"
                    className={`sidebar-individual${
                      selectedIndividual === individual.id ? " directory-tree-item-active" : ""
                    }`}
                    onClick={() => onSelectIndividual(individual.id)}
                  >
                    {individual.has_representative_thumbnail ? (
                      <img className="sidebar-individual-avatar" src={individualThumbnailUrl(individual.id)} alt="" />
                    ) : (
                      // No tagged face yet: an initial stands in for the avatar.
                      <span className="sidebar-individual-avatar sidebar-individual-initial" aria-hidden="true">
                        {individual.name.trim().charAt(0).toUpperCase()}
                      </span>
                    )}
                    {individual.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="By Tag" icon="tag" expanded={tagsExpanded} onToggle={() => setTagsExpanded((prev) => !prev)}>
          {tags.length === 0 ? (
            <p className="sidebar-individuals-empty">No tags created yet.</p>
          ) : (
            <ul className="directory-tree">
              {tags.map((tag) => (
                <li key={tag.id}>
                  <div
                    className={`directory-tree-item${selectedTag === tag.id ? " directory-tree-item-active" : ""}`}
                    data-count={tag.photo_count > 0 ? tag.photo_count.toLocaleString() : undefined}
                  >
                    <span className="directory-tree-toggle-spacer" aria-hidden="true" />
                    <button type="button" className="directory-tree-label" onClick={() => onSelectTag(tag.id)}>
                      {tag.name}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="By Date" icon="calendar" expanded={datesExpanded} onToggle={() => setDatesExpanded((prev) => !prev)}>
          <DateTree node={dateTree} path={null} selectedDate={selectedDate} onSelectDate={onSelectDate} />
        </Section>
      </nav>

      <div className="sidebar-footer">
        {/* Search is pinned here, directly above "Manage", rather than in
            the scrolling list of sections above -- it stays in reach
            however long the folder/date trees get. */}
        <Section title="Search" icon="search" expanded={searchExpanded} onToggle={() => setSearchExpanded((prev) => !prev)}>
          <SearchSection
            query={searchQuery}
            onQueryChange={onSearchQueryChange}
            threshold={searchThreshold}
            onThresholdChange={onSearchThresholdChange}
            onSearch={onSearch}
            onClear={onClearSearch}
            active={searchActive}
            loading={searchLoading}
            error={searchError}
          />
        </Section>
        <a
          href="#manage"
          className="sidebar-manage-link"
          onClick={(event) => {
            event.preventDefault();
            onManage();
          }}
        >
          <Icon name="sliders" />
          Manage
        </a>
      </div>
    </aside>
  );
}
