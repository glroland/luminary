// Split into two pieces (rather than one combined component) so the header
// controls (App.jsx) can lay them out in a specific left-to-right order
// alongside SortControl.jsx -- there's no longer a single ".pagination"
// wrapper enforcing their relative position.

export function PageSizeSelect({ pageSize, pageSizeOptions, onPageSizeChange }) {
  return (
    <label className="pagination-page-size">
      Per page
      <select value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
        {pageSizeOptions.map((size) => (
          <option key={size} value={size}>
            {size}
          </option>
        ))}
      </select>
    </label>
  );
}

export function PaginationNav({ page, totalPages, onPageChange }) {
  return (
    <div className="pagination-nav">
      <button type="button" onClick={() => onPageChange(page - 1)} disabled={page <= 1}>
        Previous
      </button>
      <span className="pagination-status">
        Page {page} of {totalPages}
      </span>
      <button type="button" onClick={() => onPageChange(page + 1)} disabled={page >= totalPages}>
        Next
      </button>
    </div>
  );
}
