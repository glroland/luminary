// One background job's button + progress, driven entirely by the server's
// `GET /api/jobs` snapshot (see App.jsx), so every browser shows the same
// thing. Only one job runs at a time server-side:
//   - idle: the start button (disabled while *another* job runs);
//   - this job running: the button becomes "Cancel", plus a progress bar;
//   - cancel requested: "Cancelling" (disabled) until the server has
//     really stopped, then it flips back to the start button.
// `owns` picks this control's share of a kind that serves two buttons
// (face matching after a sync vs. the danger-zone full refresh). A control
// with no `idleLabel` renders nothing while idle.

function percent(state) {
  return state.total > 0 ? Math.round((state.processed / state.total) * 100) : 0;
}

export default function JobControl({
  kind,
  jobs,
  justFinished,
  owns = () => true,
  idleLabel,
  buttonClass = "refresh-button",
  running,
  done,
  failed,
  onStart,
  onCancel,
}) {
  const state = jobs?.[kind];
  const mine = Boolean(state) && owns(state);
  const active = jobs?.active ?? null;

  if (mine && active === kind) {
    const pct = percent(state);
    return (
      <>
        <button type="button" className={buttonClass} onClick={onCancel} disabled={jobs.cancelling}>
          {jobs.cancelling ? "Cancelling" : "Cancel"}
        </button>
        <div className="sync-status" role="status" aria-live="polite">
          <span className="sync-status-label">{running(state, pct)}</span>
          <div className="sync-progress-track">
            <div className="sync-progress-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>
      </>
    );
  }

  let outcome = null;
  if (mine && state.status === "error") {
    outcome = (
      <div className="sync-status sync-status-error" role="status">
        {failed(state)}
      </div>
    );
  } else if (mine && justFinished === kind && (state.status === "completed" || state.status === "cancelled")) {
    outcome = (
      <div className="sync-status" role="status">
        {state.status === "cancelled" ? "Cancelled" : done(state)}
      </div>
    );
  }

  return (
    <>
      {idleLabel && (
        <button
          type="button"
          className={buttonClass}
          onClick={onStart}
          disabled={active !== null}
          title={active !== null ? "Another job is running" : undefined}
        >
          {idleLabel}
        </button>
      )}
      {outcome}
    </>
  );
}
