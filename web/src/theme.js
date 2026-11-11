// Light/dark theme: a `data-theme` attribute on <html> that styles.css's
// token blocks key off (dark is the default there, so an unset/unknown
// value still renders dark). The choice is remembered per browser in
// localStorage -- purely a display preference, nothing the API knows
// about. Storage access is wrapped because it throws outright in some
// contexts (private windows, blocked site data); the theme then just
// doesn't persist.
const STORAGE_KEY = "luminary-theme";
export const DEFAULT_THEME = "dark";

export function getStoredTheme() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // See module comment.
  }
}
