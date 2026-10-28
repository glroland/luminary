export default function SyncStatus({ status, onRefresh }) {
  if (!status || status.status === "idle") {
    return (
      <button type="button" className="refresh-button" onClick={onRefresh}>
        Refresh
      </button>
    );
  }

  if (status.status === "error") {
    return (
      <div className="sync-status sync-status-error" role="status">
        Sync failed
      </div>
    );
  }

  const percent = status.total > 0 ? Math.round((status.processed / status.total) * 100) : 0;
  const label = status.status === "completed" ? "Done" : `Syncing ${percent}%`;

  return (
    <div className="sync-status" role="status" aria-live="polite">
      <span className="sync-status-label">{label}</span>
      <div className="sync-progress-track">
        <div className="sync-progress-fill" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
