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
    onToggle: vi.fn(),
    onClose: vi.fn(),
    photos: PHOTOS,
    selectedDirectory: null,
    onSelectDirectory: vi.fn(),
    syncStatus: null,
    onRefresh: vi.fn(),
    onManage: vi.fn(),
    ...overrides,
  };
  render(<Sidebar {...props} />);
  return props;
}

describe("Sidebar", () => {
  it("calls onToggle when the hamburger button is clicked", () => {
    const props = renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    expect(props.onToggle).toHaveBeenCalled();
  });

  it("builds a directory tree from the photos' filenames", () => {
    renderSidebar({ open: true });
    expect(screen.getByText("vacation")).toBeInTheDocument();
    expect(screen.getByText("work")).toBeInTheDocument();
  });

  it("does not render a backdrop when closed", () => {
    renderSidebar({ open: false });
    expect(document.querySelector(".sidebar-backdrop")).not.toBeInTheDocument();
  });

  it("renders a backdrop when open, and closes on clicking it", () => {
    const props = renderSidebar({ open: true });
    fireEvent.click(document.querySelector(".sidebar-backdrop"));
    expect(props.onClose).toHaveBeenCalled();
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

  it("renders the Refresh button from SyncStatus when idle", () => {
    renderSidebar({ open: true, syncStatus: null });
    expect(screen.getByRole("button", { name: /refresh/i })).toBeInTheDocument();
  });
});
