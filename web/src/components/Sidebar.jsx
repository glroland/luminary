import { useMemo, useState } from "react";
import { individualThumbnailUrl } from "../api/client.js";
import DirectoryTree from "./DirectoryTree.jsx";

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
// Three parent sections narrow the photo grid, mutually exclusive with
// each other (selecting one clears any other filter -- see App.jsx's
// handleSelectDirectory/handleSelectIndividual/handleAllImages):
// "All Images" clears every filter, "Folders" filters by directory (via
// DirectoryTree, built from the photos' own paths), and "Individuals"
// filters to photos containing a selected catalogued person.
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
}) {
  const tree = useMemo(() => buildTree(photos), [photos]);
  const [foldersExpanded, setFoldersExpanded] = useState(true);
  const [individualsExpanded, setIndividualsExpanded] = useState(true);
  const noFilter = selectedDirectory === null && selectedIndividual === null;

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
