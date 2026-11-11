import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
import { applyTheme, getStoredTheme } from "./theme.js";

// Before the first render, so a stored light preference doesn't flash dark.
applyTheme(getStoredTheme());

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
