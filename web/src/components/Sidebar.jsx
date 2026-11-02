import { useMemo, useState } from "react";
import { individualThumbnailUrl } from "../api/client.js";
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
  const root = { children: {} };
  for (const photo of photos) {
    const parts = photo.filename.split("/");
    parts.pop(); // the filename itself, not a directory
    let node = root;
    for (const part of parts) {
      node.children[part] ??= { children: {} };
      node = node.children[part];
    }
  }
  return root;
}

// A collapsible parent section ("Folders", "Individuals") -- separate from
// each individual DirectoryTree node's own expand/collapse (also +/−
// style, see DirectoryTree.jsx), this just shows/hides the section's whole
// contents so a long individuals list doesn't dominate the sidebar.
function Section({ title, expanded, onToggle, children }) {
  return (
    <div className="sidebar-section">
      <button
        type="button"
        className="sidebar-section-header"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="sidebar-section-toggle" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
        {title}
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
// Four parent sections narrow the photo grid, mutually exclusive with each
// other (selecting one clears any other filter -- see App.jsx's
// handleSelectDirectory/handleSelectIndividual/handleAllImages/
// handleSearch): "All Images" clears every filter, "Folders" filters by
// directory (via DirectoryTree, built from the photos' own paths),
// "Individuals" filters to photos containing a selected catalogued
// person, and "Search" (below Individuals) narrows to photos whose
// description is similar enough to a free-text query (embeddings.py's
// similarity search), sorted most-similar first -- that ordering comes
// from the API response itself (GET /api/photos/search), not anything
// sorted client-side here.
export default function Sidebar({
  open,
  photos,
  selectedDirectory,
  onSelectDirectory,
  individuals,
  selectedIndividual,
  onSelectIndividual,
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
  const [foldersExpanded, setFoldersExpanded] = useState(true);
  const [individualsExpanded, setIndividualsExpanded] = useState(true);
  const [searchExpanded, setSearchExpanded] = useState(true);
  const noFilter = selectedDirectory === null && selectedIndividual === null && !searchActive;

  return (
    <aside className={`sidebar${open ? " sidebar-open" : ""}`}>
      <nav className="sidebar-tree">
        <button
          type="button"
          className={`sidebar-all-images${noFilter ? " sidebar-all-images-active" : ""}`}
          onClick={onAllImages}
        >
          All Images
        </button>

        <Section title="Folders" expanded={foldersExpanded} onToggle={() => setFoldersExpanded((prev) => !prev)}>
          <DirectoryTree
            node={tree}
            path={null}
            selectedDirectory={selectedDirectory}
            onSelectDirectory={onSelectDirectory}
          />
        </Section>

        <Section
          title="Individuals"
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
                    {individual.has_representative_thumbnail && (
                      <img className="sidebar-individual-avatar" src={individualThumbnailUrl(individual.id)} alt="" />
                    )}
                    {individual.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Search" expanded={searchExpanded} onToggle={() => setSearchExpanded((prev) => !prev)}>
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
      </nav>

      <div className="sidebar-footer">
        <a
          href="#manage"
          className="sidebar-manage-link"
          onClick={(event) => {
            event.preventDefault();
            onManage();
          }}
        >
          Manage
        </a>
      </div>
    </aside>
  );
}
