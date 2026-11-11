import { useState } from "react";

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

// A date node is {children: {key: node}} -- see Sidebar.jsx's buildDateTree,
// which derives this from each photo's created_at. Three levels deep (year
// -> month -> day), with keys zero-padded ("05", not "5") so both the
// lexical sort below and the "-"-joined path built here line up with
// App.jsx's dateKeyOf/startsWith filtering.
function formatLabel(depth, key) {
  if (depth === 1) return MONTH_NAMES[Number(key) - 1];
  if (depth === 2) return String(Number(key)); // day: drop the zero-padding for display
  return key; // year
}

export default function DateTree({ node, path, depth = 0, selectedDate, onSelectDate }) {
  // Years newest-first; months/days chronological within their parent.
  const keys = Object.keys(node.children).sort((a, b) => (depth === 0 ? b.localeCompare(a) : a.localeCompare(b)));
  if (keys.length === 0) return null;

  return (
    <ul className="directory-tree">
      {keys.map((key) => (
        <DateNode
          key={key}
          dateKey={key}
          node={node.children[key]}
          path={path ? `${path}-${key}` : key}
          depth={depth}
          selectedDate={selectedDate}
          onSelectDate={onSelectDate}
        />
      ))}
    </ul>
  );
}

function DateNode({ dateKey, node, path, depth, selectedDate, onSelectDate }) {
  const [expanded, setExpanded] = useState(false);
  const hasChildren = Object.keys(node.children).length > 0;
  const isActive = selectedDate === path;

  return (
    <li>
      <div className={`directory-tree-item${isActive ? " directory-tree-item-active" : ""}`}>
        {hasChildren ? (
          <button
            type="button"
            className="directory-tree-toggle"
            onClick={() => setExpanded((prev) => !prev)}
            aria-label={expanded ? "Collapse" : "Expand"}
            aria-expanded={expanded}
          >
            {expanded ? "−" : "+"}
          </button>
        ) : (
          <span className="directory-tree-toggle-spacer" aria-hidden="true" />
        )}
        <button type="button" className="directory-tree-label" onClick={() => onSelectDate(path)}>
          {formatLabel(depth, dateKey)}
        </button>
      </div>
      {hasChildren && expanded && (
        <DateTree node={node} path={path} depth={depth + 1} selectedDate={selectedDate} onSelectDate={onSelectDate} />
      )}
    </li>
  );
}
