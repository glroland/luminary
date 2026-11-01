export default function Pagination({ page, totalPages, pageSize, pageSizeOptions, onPageChange, onPageSizeChange }) {
  return (
    <div className="pagination">
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
    </div>
  );
}
