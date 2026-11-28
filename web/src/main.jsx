import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { AuthProvider } from "./auth.jsx";
import "./styles.css";
import { applyTheme, getStoredTheme } from "./theme.js";

// The gallery is windowed (only the first batch renders, more on scroll), so
// the browser's restored scroll offset on refresh lands somewhere that
// doesn't correspond to the content, leaving the view mid-section with no
// header. Always start from the top, like a fresh load.
try {
  if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
} catch {
  // Not available (very old browser / sandboxed frame): harmless.
}

// Before the first render, so a stored light preference doesn't flash dark.
applyTheme(getStoredTheme());

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>
);
