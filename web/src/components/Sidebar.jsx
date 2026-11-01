import { useMemo } from "react";
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

// A traditional, non-overlaying sidebar: it occupies its own column in the
// page's flex layout (see App.jsx/styles.css's ".app"/".sidebar" rules),
// flush against the window's left edge, rather than floating a
// fixed-position panel over the main content -- so it can be left open
// indefinitely without blocking interaction with the gallery/management
// views beside it. The hamburger toggle that opens/closes it lives in
// App.jsx's header, next to the logo, not here -- it has to stay visible
// and clickable regardless of open/closed state, which it couldn't do as
// part of this collapsing <aside>.
export default function Sidebar({ open, photos, selectedDirectory, onSelectDirectory, onManage }) {
  const tree = useMemo(() => buildTree(photos), [photos]);

  return (
    <aside className={`sidebar${open ? " sidebar-open" : ""}`}>
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
  );
}
