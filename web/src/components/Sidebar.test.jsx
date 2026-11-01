import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Sidebar from "./Sidebar.jsx";

const PHOTOS = [
  { id: "1", filename: "vacation/2019/beach.jpg" },
  { id: "2", filename: "work/receipt.jpg" },
];

const INDIVIDUALS = [
  { id: "p1", name: "Alice", has_representative_thumbnail: false },
  { id: "p2", name: "Bob", has_representative_thumbnail: true },
];

function renderSidebar(overrides = {}) {
  const props = {
    open: false,
    photos: PHOTOS,
    selectedDirectory: null,
    onSelectDirectory: vi.fn(),
    individuals: INDIVIDUALS,
    selectedIndividual: null,
    onSelectIndividual: vi.fn(),
    onAllImages: vi.fn(),
    onManage: vi.fn(),
    ...overrides,
  };
  render(<Sidebar {...props} />);
  return props;
}

describe("Sidebar", () => {
  it("builds a directory tree from the photos' filenames, under the Folders section", () => {
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

  it("calls onAllImages when 'All Images' is clicked", () => {
    const props = renderSidebar({ open: true, selectedDirectory: "work" });
    fireEvent.click(screen.getByText("All Images"));
    expect(props.onAllImages).toHaveBeenCalled();
  });

  it("marks 'All Images' active only when no filter is selected", () => {
    const { rerender } = render(<Sidebar {...propsFor({ selectedDirectory: null, selectedIndividual: null })} />);
    expect(screen.getByText("All Images")).toHaveClass("sidebar-all-images-active");

    rerender(<Sidebar {...propsFor({ selectedDirectory: "work", selectedIndividual: null })} />);
    expect(screen.getByText("All Images")).not.toHaveClass("sidebar-all-images-active");
  });

  it("selects a directory via the Folders tree", () => {
    const props = renderSidebar({ open: true });
    fireEvent.click(screen.getByText("work"));
    expect(props.onSelectDirectory).toHaveBeenCalledWith("work");
  });

  it("lists catalogued individuals under the Individuals section", () => {
    renderSidebar({ open: true });
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
  });

  it("shows an empty-state message when there are no individuals", () => {
    renderSidebar({ open: true, individuals: [] });
    expect(screen.getByText(/no individuals catalogued yet/i)).toBeInTheDocument();
  });

  it("calls onSelectIndividual when an individual is clicked", () => {
    const props = renderSidebar({ open: true });
    fireEvent.click(screen.getByText("Alice"));
    expect(props.onSelectIndividual).toHaveBeenCalledWith("p1");
  });

  it("marks the selected individual active", () => {
    renderSidebar({ open: true, selectedIndividual: "p2" });
    expect(screen.getByText("Bob").closest("button")).toHaveClass("directory-tree-item-active");
    expect(screen.getByText("Alice").closest("button")).not.toHaveClass("directory-tree-item-active");
  });

  it("collapses and expands the Individuals section", () => {
    renderSidebar({ open: true });
    expect(screen.getByText("Alice")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /individuals/i }));
    expect(screen.queryByText("Alice")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /individuals/i }));
    expect(screen.getByText("Alice")).toBeInTheDocument();
  });

  it("collapses and expands the Folders section", () => {
    renderSidebar({ open: true });
    expect(screen.getByText("vacation")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^folders$/i }));
    expect(screen.queryByText("vacation")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^folders$/i }));
    expect(screen.getByText("vacation")).toBeInTheDocument();
  });

  it("calls onManage when the Manage link is clicked", () => {
    const props = renderSidebar({ open: true });
    fireEvent.click(screen.getByText("Manage"));
    expect(props.onManage).toHaveBeenCalled();
  });
});

function propsFor(overrides) {
  return {
    open: true,
    photos: PHOTOS,
    selectedDirectory: null,
    onSelectDirectory: vi.fn(),
    individuals: INDIVIDUALS,
    selectedIndividual: null,
    onSelectIndividual: vi.fn(),
    onAllImages: vi.fn(),
    onManage: vi.fn(),
    ...overrides,
  };
}
