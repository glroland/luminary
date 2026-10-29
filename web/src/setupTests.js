import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement ResizeObserver -- PhotoModal.jsx uses one to
// track the displayed photo's rendered size for its bounding-box overlay.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
