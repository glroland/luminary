import { useMemo } from "react";
import DirectoryTree from "./DirectoryTree.jsx";
import SyncStatus from "./SyncStatus.jsx";

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

export default function Sidebar({
  open,
  onToggle,
  onClose,
  photos,
  selectedDirectory,
  onSelectDirectory,
  syncStatus,
  onRefresh,
  onManage,
}) {
  const tree = useMemo(() => buildTree(photos), [photos]);

  return (
    <>
      <button
        type="button"
        className="sidebar-toggle"
        onClick={onToggle}
        aria-label="Toggle sidebar"
        aria-expanded={open}
      >
        <span className="sidebar-toggle-icon" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      </button>

      {/* Fixed-position overlay + backdrop -- rendered here regardless of
          where Sidebar sits in the DOM (inside app-header, see App.jsx),
          since position: fixed doesn't care about ancestor layout. */}
      {open && <div className="sidebar-backdrop" onClick={onClose} />}

      <aside className={`sidebar${open ? " sidebar-open" : ""}`}>
        <div className="sidebar-refresh">
          <SyncStatus status={syncStatus} onRefresh={onRefresh} />
        </div>

        <nav className="sidebar-tree">
          <button
            type="button"
            className={`sidebar-tree-root${selectedDirectory === null ? " directory-tree-item-active" : ""}`}
            onClick={() => onSelectDirectory(null)}
          >
            All Photos
          </button>
          <DirectoryTree
            node={tree}
            path={null}
            selectedDirectory={selectedDirectory}
            onSelectDirectory={onSelectDirectory}
          />
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
    </>
  );
}
