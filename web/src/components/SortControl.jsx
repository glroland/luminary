// Sort options for the gallery grid (App.jsx) -- date defaults to newest
// first, matching the API's own fixed ORDER BY created_at DESC, so picking
// this option changes nothing about what's on screen. Not shown while a
// search is active: GET /api/photos/search's results are already ordered
// most-similar-first, and re-sorting them by date/filename would throw that
// relevance ordering away.
export const SORT_OPTIONS = [
  { value: "date-desc", label: "Date (newest first)" },
  { value: "date-asc", label: "Date (oldest first)" },
  { value: "filename-asc", label: "Filename (A-Z)" },
  { value: "filename-desc", label: "Filename (Z-A)" },
  { value: "folder-asc", label: "Folder (A-Z)" },
  { value: "folder-desc", label: "Folder (Z-A)" },
];

export default function SortControl({ value, onChange }) {
  return (
    <label className="sort-control">
      Sort by
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {SORT_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
