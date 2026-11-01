import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Sidebar from "./Sidebar.jsx";

const PHOTOS = [
  { id: "1", filename: "vacation/2019/beach.jpg" },
  { id: "2", filename: "work/receipt.jpg" },
];

function renderSidebar(overrides = {}) {
  const props = {
    open: false,
    photos: PHOTOS,
    selectedDirectory: null,
    onSelectDirectory: vi.fn(),
    onManage: vi.fn(),
    ...overrides,
  };
  render(<Sidebar {...props} />);
  return props;
}

describe("Sidebar", () => {
  it("builds a directory tree from the photos' filenames", () => {
    renderSidebar({ open: true });
    expect(screen.getByText("vacation")).toBeInTheDocument();
    expect(screen.getByText("work")).toBeInTheDocument();
  });

  // The sidebar is a traditional, non-overlaying panel (see styles.css's
  // ".sidebar"/".sidebar-open" rules) -- it has no backdrop at any open
  // state. The hamburger toggle that opens/closes it lives in App.jsx's
  // header now, not here -- see App.test.jsx for that behavior.
  it("renders no backdrop, open or closed", () => {
    renderSidebar({ open: false });
    expect(document.querySelector(".sidebar-backdrop")).not.toBeInTheDocument();
    renderSidebar({ open: true });
    expect(document.querySelector(".sidebar-backdrop")).not.toBeInTheDocument();
  });

  it("clears the directory filter when 'All Photos' is clicked", () => {
    const props = renderSidebar({ open: true, selectedDirectory: "work" });
    fireEvent.click(screen.getByText("All Photos"));
    expect(props.onSelectDirectory).toHaveBeenCalledWith(null);
  });

  it("calls onManage when the Manage link is clicked", () => {
    const props = renderSidebar({ open: true });
    fireEvent.click(screen.getByText("Manage"));
    expect(props.onManage).toHaveBeenCalled();
  });
});
