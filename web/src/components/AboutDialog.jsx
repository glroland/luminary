import { useEffect, useState } from "react";
import { fetchAbout } from "../api/client.js";
import Logo from "./Logo.jsx";

// The version is the Jenkins build number baked into the container image
// (api/Containerfile's LUMINARY_VERSION); the API reports none when it's
// running from source, which is shown as development mode.
export default function AboutDialog({ onClose }) {
  const [about, setAbout] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchAbout()
      .then((data) => !cancelled && setAbout(data))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const rows = about
    ? [
        ["Photo album source", about.photos_source],
        ["Chat model", about.chat_model || "Not configured"],
        ...(about.chat_endpoint ? [["Model endpoint", about.chat_endpoint]] : []),
        ["Database", about.database_name],
        ["Database server", about.database_host],
      ]
    : [];

  return (
    <div className="about-backdrop" onClick={onClose}>
      <div
        className="about-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="about-title"
        onClick={(event) => event.stopPropagation()}
      >
        <button type="button" className="about-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2 id="about-title" className="sr-only" style={{ position: "absolute", left: "-9999px" }}>
          About Luminary
        </h2>
        <div className="about-logo">
          <Logo />
        </div>
        {error && <p role="alert">{error}</p>}
        {!about && !error && <p className="about-version">Loading…</p>}
        {about && (
          <>
            <p className="about-version">
              Version: {about.version ? about.version : "Development mode"}
            </p>
            <dl className="about-details">
              {rows.map(([label, value]) => (
                <div key={label} style={{ display: "contents" }}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </>
        )}
        {about && (
          <p className="about-copyright">© {about.year} Lee Roland. All rights reserved.</p>
        )}
      </div>
    </div>
  );
}
