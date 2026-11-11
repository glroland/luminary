import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Sidebar from "./Sidebar.jsx";

const PHOTOS = [
  { id: "1", filename: "vacation/2019/beach.jpg", created_at: "2019-07-04T12:00:00Z" },
  { id: "2", filename: "work/receipt.jpg", created_at: "2023-01-15T08:00:00Z" },
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
    selectedDate: null,
    onSelectDate: vi.fn(),
    onAllImages: vi.fn(),
    onManage: vi.fn(),
    searchQuery: "",
    onSearchQueryChange: vi.fn(),
    searchThreshold: 0.5,
    onSearchThresholdChange: vi.fn(),
    onSearch: vi.fn(),
    onClearSearch: vi.fn(),
    searchActive: false,
    searchLoading: false,
    searchError: null,
    ...overrides,
  };
  render(<Sidebar {...props} />);
  return props;
}

// All three tree sections (By Folder/By Individual/By Date) default to
// collapsed on a fresh render -- see Sidebar.jsx's useState(false) for
// each -- so most tests below expand the relevant section first, same as
// a real user clicking it open.
function expandSection(name) {
  fireEvent.click(screen.getByRole("button", { name }));
}

describe("Sidebar", () => {
  it("defaults the By Folder, By Individual, and By Date sections to collapsed", () => {
    renderSidebar({ open: true });
    expect(screen.queryByText("vacation")).not.toBeInTheDocument();
    expect(screen.queryByText("Alice")).not.toBeInTheDocument();
    expect(screen.queryByText("2019")).not.toBeInTheDocument();
  });

  it("builds a directory tree from the photos' filenames, under the Folders section", () => {
    renderSidebar({ open: true });
    expandSection(/^by folder$/i);
    expect(screen.getByText("vacation")).toBeInTheDocument();
    expect(screen.getByText("work")).toBeInTheDocument();
  });

  it("always shows a (Root) entry under By Folder, even with no root-level photos", () => {
    renderSidebar({ open: true });
    expandSection(/^by folder$/i);
    expect(screen.getByText("(Root)")).toBeInTheDocument();
  });

  it("shows the (Root) entry under By Folder even when there are no folders at all", () => {
    renderSidebar({
      open: true,
      photos: [{ id: "1", filename: "selfie.jpg", created_at: "2023-01-15T08:00:00Z" }],
    });
    expandSection(/^by folder$/i);
    expect(screen.getByText("(Root)")).toBeInTheDocument();
    expect(screen.queryByText("vacation")).not.toBeInTheDocument();
  });

  it("selects the root directory when (Root) is clicked", () => {
    const props = renderSidebar({ open: true });
    expandSection(/^by folder$/i);
    fireEvent.click(screen.getByText("(Root)"));
    expect(props.onSelectDirectory).toHaveBeenCalledWith("");
  });

  it("marks (Root) active only when selectedDirectory is exactly \"\"", () => {
    const { rerender } = render(<Sidebar {...propsFor({ selectedDirectory: "" })} />);
    fireEvent.click(screen.getByRole("button", { name: /^by folder$/i }));
    expect(screen.getByText("(Root)").closest(".directory-tree-item")).toHaveClass("directory-tree-item-active");

    rerender(<Sidebar {...propsFor({ selectedDirectory: null })} />);
    expect(screen.getByText("(Root)").closest(".directory-tree-item")).not.toHaveClass("directory-tree-item-active");
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
    expandSection(/^by folder$/i);
    fireEvent.click(screen.getByText("work"));
    expect(props.onSelectDirectory).toHaveBeenCalledWith("work");
  });

  it("lists catalogued individuals under the Individuals section", () => {
    renderSidebar({ open: true });
    expandSection(/^by individual$/i);
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
  });

  it("shows an empty-state message when there are no individuals", () => {
    renderSidebar({ open: true, individuals: [] });
    expandSection(/^by individual$/i);
    expect(screen.getByText(/no individuals catalogued yet/i)).toBeInTheDocument();
  });

  it("calls onSelectIndividual when an individual is clicked", () => {
    const props = renderSidebar({ open: true });
    expandSection(/^by individual$/i);
    fireEvent.click(screen.getByText("Alice"));
    expect(props.onSelectIndividual).toHaveBeenCalledWith("p1");
  });

  it("marks the selected individual active", () => {
    renderSidebar({ open: true, selectedIndividual: "p2" });
    expandSection(/^by individual$/i);
    expect(screen.getByText("Bob").closest("button")).toHaveClass("directory-tree-item-active");
    expect(screen.getByText("Alice").closest("button")).not.toHaveClass("directory-tree-item-active");
  });

  it("collapses and expands the Individuals section", () => {
    renderSidebar({ open: true });
    expect(screen.queryByText("Alice")).not.toBeInTheDocument();

    expandSection(/^by individual$/i);
    expect(screen.getByText("Alice")).toBeInTheDocument();

    expandSection(/^by individual$/i);
    expect(screen.queryByText("Alice")).not.toBeInTheDocument();
  });

  it("collapses and expands the Folders section", () => {
    renderSidebar({ open: true });
    expect(screen.queryByText("vacation")).not.toBeInTheDocument();

    expandSection(/^by folder$/i);
    expect(screen.getByText("vacation")).toBeInTheDocument();

    expandSection(/^by folder$/i);
    expect(screen.queryByText("vacation")).not.toBeInTheDocument();
  });

  it("builds a year -> month -> day tree from the photos' created_at, under the By Date section", () => {
    renderSidebar({ open: true });
    expandSection(/^by date$/i);
    expect(screen.getByText("2019")).toBeInTheDocument();
    expect(screen.getByText("2023")).toBeInTheDocument();
    expect(screen.queryByText("July")).not.toBeInTheDocument(); // the year node itself isn't expanded yet
  });

  it("selects a year via the By Date tree", () => {
    const props = renderSidebar({ open: true });
    expandSection(/^by date$/i);
    fireEvent.click(screen.getByText("2023"));
    expect(props.onSelectDate).toHaveBeenCalledWith("2023");
  });

  it("drills down the By Date tree to select a month, then a day", () => {
    const props = renderSidebar({ open: true });
    expandSection(/^by date$/i);

    // Expand the 2019 year node to reveal its month.
    fireEvent.click(screen.getByText("2019").closest("li").querySelector(".directory-tree-toggle"));
    fireEvent.click(screen.getByText("July"));
    expect(props.onSelectDate).toHaveBeenCalledWith("2019-07");

    // Expand the July month node to reveal its day.
    fireEvent.click(screen.getByText("July").closest("li").querySelector(".directory-tree-toggle"));
    fireEvent.click(screen.getByText("4"));
    expect(props.onSelectDate).toHaveBeenCalledWith("2019-07-04");
  });

  it("marks the selected date node active", () => {
    renderSidebar({ open: true, selectedDate: "2023" });
    expandSection(/^by date$/i);
    expect(screen.getByText("2023").closest(".directory-tree-item")).toHaveClass("directory-tree-item-active");
    expect(screen.getByText("2019").closest(".directory-tree-item")).not.toHaveClass("directory-tree-item-active");
  });

  it("collapses and expands the By Date section", () => {
    renderSidebar({ open: true });
    expect(screen.queryByText("2019")).not.toBeInTheDocument();

    expandSection(/^by date$/i);
    expect(screen.getByText("2019")).toBeInTheDocument();

    expandSection(/^by date$/i);
    expect(screen.queryByText("2019")).not.toBeInTheDocument();
  });

  it("calls onManage when the Manage link is clicked", () => {
    const props = renderSidebar({ open: true });
    fireEvent.click(screen.getByText("Manage"));
    expect(props.onManage).toHaveBeenCalled();
  });

  // The search section's own submit button is also named "Search", same
  // as the collapsible section header above it -- query it by its
  // distinguishing type="submit" rather than getByRole("button", {name})
  // to avoid matching both.
  function searchSubmitButton() {
    return document.querySelector(".sidebar-search-actions button[type='submit']");
  }

  it("renders a Search section under Individuals", () => {
    renderSidebar({ open: true });
    expect(searchSubmitButton()).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/search photo descriptions/i)).toBeInTheDocument();
  });

  it("disables the Search button until a query is typed", () => {
    // Sidebar is fully controlled -- searchQuery/onQueryChange are the
    // parent's (App.jsx's) state, not local -- so simulating "typing" here
    // means rerendering with a new searchQuery prop, the same way a real
    // onChange round-trip through the parent would.
    const { rerender } = render(<Sidebar {...propsFor({ searchQuery: "" })} />);
    expect(searchSubmitButton()).toBeDisabled();

    rerender(<Sidebar {...propsFor({ searchQuery: "a cat" })} />);
    expect(searchSubmitButton()).not.toBeDisabled();
  });

  it("notifies onSearchQueryChange when the query textarea is edited", () => {
    const props = renderSidebar({ open: true });
    fireEvent.change(screen.getByPlaceholderText(/search photo descriptions/i), {
      target: { value: "a cat" },
    });
    expect(props.onSearchQueryChange).toHaveBeenCalledWith("a cat");
  });

  it("submits the typed query and threshold via onSearch/onQueryChange/onThresholdChange", () => {
    const props = renderSidebar({ open: true, searchQuery: "a cat", searchThreshold: 0.7 });

    expect(screen.getByDisplayValue("a cat")).toBeInTheDocument();
    expect(screen.getByLabelText(/similarity threshold/i)).toHaveValue("0.7");

    fireEvent.click(searchSubmitButton());
    expect(props.onSearch).toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/similarity threshold/i), { target: { value: "0.85" } });
    expect(props.onSearchThresholdChange).toHaveBeenCalledWith(0.85);
  });

  it("only shows the clear ('x') button once a search is active", () => {
    renderSidebar({ open: true, searchActive: false });
    expect(screen.queryByRole("button", { name: /clear search/i })).not.toBeInTheDocument();

    const props = renderSidebar({ open: true, searchActive: true });
    const clearButton = screen.getByRole("button", { name: /clear search/i });
    fireEvent.click(clearButton);
    expect(props.onClearSearch).toHaveBeenCalled();
  });

  it("shows a search error when provided", () => {
    renderSidebar({ open: true, searchError: "Search backend failed" });
    expect(screen.getByRole("alert")).toHaveTextContent("Search backend failed");
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
    selectedDate: null,
    onSelectDate: vi.fn(),
    onAllImages: vi.fn(),
    onManage: vi.fn(),
    searchQuery: "",
    onSearchQueryChange: vi.fn(),
    searchThreshold: 0.5,
    onSearchThresholdChange: vi.fn(),
    onSearch: vi.fn(),
    onClearSearch: vi.fn(),
    searchActive: false,
    searchLoading: false,
    searchError: null,
    ...overrides,
  };
}
