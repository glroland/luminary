import { useState } from "react";

// A directory node is {children: {name: node}} -- see Sidebar.jsx's
// buildTree, which derives this purely from photo.filename paths (there's
// no dedicated "directory" concept in the API, just the photos' own
// relative paths).
export default function DirectoryTree({ node, path, selectedDirectory, onSelectDirectory }) {
  const names = Object.keys(node.children).sort((a, b) => a.localeCompare(b));
  if (names.length === 0) return null;

  return (
    <ul className="directory-tree">
      {names.map((name) => (
        <DirectoryNode
          key={name}
          name={name}
          node={node.children[name]}
          path={path ? `${path}/${name}` : name}
          selectedDirectory={selectedDirectory}
          onSelectDirectory={onSelectDirectory}
        />
      ))}
    </ul>
  );
}

function DirectoryNode({ name, node, path, selectedDirectory, onSelectDirectory }) {
  // Expand/collapse is local to each node (uncontrolled) -- there's no
  // need to lift it any higher, only the *selected* directory (which
  // filters the photo grid) is shared state.
  const [expanded, setExpanded] = useState(false);
  const hasChildren = Object.keys(node.children).length > 0;
  const isActive = selectedDirectory === path;

  return (
    <li>
      <div
        className={`directory-tree-item${isActive ? " directory-tree-item-active" : ""}`}
        // The photo count is a data attribute styles.css renders with
        // ::after, not text in the DOM, so the label button's accessible
        // name (and what tests match on) stays just the label.
        data-count={node.count ? node.count.toLocaleString() : undefined}
      >
        {hasChildren ? (
          <button
            type="button"
            className="directory-tree-toggle"
            onClick={() => setExpanded((prev) => !prev)}
            aria-label={expanded ? "Collapse" : "Expand"}
            aria-expanded={expanded}
          >
            ›
          </button>
        ) : (
          <span className="directory-tree-toggle-spacer" aria-hidden="true" />
        )}
        <button type="button" className="directory-tree-label" onClick={() => onSelectDirectory(path)}>
          {name}
        </button>
      </div>
      {hasChildren && expanded && (
        <DirectoryTree node={node} path={path} selectedDirectory={selectedDirectory} onSelectDirectory={onSelectDirectory} />
      )}
    </li>
  );
}
