import { useState } from "react";
import { LOCATION_SEPARATOR } from "../locations.js";

// Tree of locations built by locations.js's buildLocationTree: region (or
// country) at the root, narrowing to locality, street and place.
export default function LocationTree({ node, path = null, selectedLocation, onSelectLocation }) {
  const keys = Object.keys(node.children).sort((a, b) => a.localeCompare(b));
  if (keys.length === 0) return null;

  return (
    <ul className="directory-tree">
      {keys.map((key) => (
        <LocationNode
          key={key}
          name={key}
          node={node.children[key]}
          path={path ? `${path}${LOCATION_SEPARATOR}${key}` : key}
          selectedLocation={selectedLocation}
          onSelectLocation={onSelectLocation}
        />
      ))}
    </ul>
  );
}

function LocationNode({ name, node, path, selectedLocation, onSelectLocation }) {
  const [expanded, setExpanded] = useState(false);
  const hasChildren = Object.keys(node.children).length > 0;

  return (
    <li>
      <div
        className={`directory-tree-item${selectedLocation === path ? " directory-tree-item-active" : ""}`}
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
        <button type="button" className="directory-tree-label" onClick={() => onSelectLocation(path)}>
          {name}
        </button>
      </div>
      {hasChildren && expanded && (
        <LocationTree node={node} path={path} selectedLocation={selectedLocation} onSelectLocation={onSelectLocation} />
      )}
    </li>
  );
}
