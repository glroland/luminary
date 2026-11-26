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

const TAGS = [
  { id: "t1", name: "Favorites", created_at: "2024-01-01T00:00:00Z", photo_count: 3 },
  { id: "t2", name: "Empty", created_at: "2024-01-01T00:00:00Z", photo_count: 0 },
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
    tags: TAGS,
    selectedTag: null,
    onSelectTag: vi.fn(),
    selectedDate: null,
    onSelectDate: vi.fn(),
    onAllImages: vi.fn(),
    onManage: vi.fn(),
    searchResultCount: null,
    searchActive: false,
    onSearchResults: vi.fn(),
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

  it("lists tags under By Tag, placed between By Folder and By Individual", () => {
    renderSidebar({ open: true });
    const headers = screen
      .getAllByRole("button", { name: /^by /i })
      .map((button) => button.textContent.replace("›", "").trim());
    expect(headers).toEqual(["By Folder", "By Tag", "By Individual", "By Location", "By Date", "By Trip"]);
    expandSection(/^by tag$/i);
    expect(screen.getByText("Favorites").closest(".directory-tree-item")).toHaveAttribute("data-count", "3");
    expect(screen.getByText("Empty").closest(".directory-tree-item")).not.toHaveAttribute("data-count");
  });

  it("lists trips by year under By Trip, hiding day outings until asked", () => {
    const trips = [
      { id: "a", name: "Italy, May 2019", kind: "trip", start_at: "2019-05-10T09:00:00", photo_count: 40 },
      { id: "b", name: "Boone, North Carolina, Oct 12, 2021", kind: "outing", start_at: "2021-10-12T09:00:00", photo_count: 12 },
      { id: "c", name: "Orlando, Florida, Dec 2019", kind: "trip", start_at: "2019-12-20T09:00:00", photo_count: 25 },
    ];
    const onToggleOutings = vi.fn();
    const onSelectTrip = vi.fn();
    renderSidebar({ open: true, trips, onToggleOutings, onSelectTrip });
    expandSection(/^by trip$/i);

    // Outings are hidden by default; only 2019 has trips.
    expect(screen.queryByText("2021")).not.toBeInTheDocument();
    expect(screen.getByText("2019").closest(".directory-tree-item")).toHaveAttribute("data-count", "65");
    fireEvent.click(screen.getByText("2019"));
    const names = screen.getAllByText(/, (May|Dec) 2019$/).map((node) => node.textContent);
    expect(names).toEqual(["Orlando, Florida, Dec 2019", "Italy, May 2019"]); // newest first
    fireEvent.click(screen.getByText("Italy, May 2019"));
    expect(onSelectTrip).toHaveBeenCalledWith("a");

    fireEvent.click(screen.getByLabelText(/show day outings/i));
    expect(onToggleOutings).toHaveBeenCalledWith(true);
  });

  it("shows day outings, and marks the selected trip, when asked to", () => {
    const trips = [{ id: "b", name: "Boone day", kind: "outing", start_at: "2021-10-12T09:00:00", photo_count: 12 }];
    renderSidebar({ open: true, trips, showOutings: true, selectedTrip: "b" });
    expandSection(/^by trip$/i);
    // The year holding the selected trip starts open.
    expect(screen.getByText("Boone day").closest(".directory-tree-item")).toHaveClass("directory-tree-item-active");
    expect(screen.getByText("All Images")).not.toHaveClass("sidebar-all-images-active");
  });

  it("explains an empty By Trip section", () => {
    renderSidebar({ open: true, trips: [] });
    expandSection(/^by trip$/i);
    expect(screen.getByText(/no trips found yet/i)).toBeInTheDocument();
  });

  it("shows an empty-state message when there are no tags", () => {
    renderSidebar({ open: true, tags: [] });
    expandSection(/^by tag$/i);
    expect(screen.getByText(/no tags created yet/i)).toBeInTheDocument();
  });

  it("calls onSelectTag when a tag is clicked, and marks it active once selected", () => {
    const props = renderSidebar({ open: true, selectedTag: "t1" });
    expandSection(/^by tag$/i);
    fireEvent.click(screen.getByText("Favorites"));
    expect(props.onSelectTag).toHaveBeenCalledWith("t1");
    expect(screen.getByText("Favorites").closest(".directory-tree-item")).toHaveClass("directory-tree-item-active");
    expect(screen.getByText("All Images")).not.toHaveClass("sidebar-all-images-active");
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

  it("has no search box -- search is done through the chat", () => {
    renderSidebar({ open: true });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("calls onSearchResults when the Search Results row is clicked", () => {
    const props = renderSidebar({ open: true });
    fireEvent.click(screen.getByRole("button", { name: "Search Results" }));
    expect(props.onSearchResults).toHaveBeenCalled();
  });

  it("marks Search Results, not All Images, as active while a search is shown", () => {
    renderSidebar({ open: true, searchActive: true, searchResultCount: 3 });
    expect(screen.getByRole("button", { name: "Search Results" })).toHaveClass("sidebar-all-images-active");
    expect(screen.getByRole("button", { name: "All Images" })).not.toHaveClass("sidebar-all-images-active");
  });

  it("carries the last search's photo count on Search Results, once there is one", () => {
    const { rerender } = render(<Sidebar {...propsFor({ searchResultCount: null })} />);
    expect(screen.getByRole("button", { name: "Search Results" })).not.toHaveAttribute("data-count");

    rerender(<Sidebar {...propsFor({ searchResultCount: 0 })} />);
    expect(screen.getByRole("button", { name: "Search Results" })).toHaveAttribute("data-count", "0");
  });

  it("carries photo counts on All Images and on folder rows (subfolders included)", () => {
    renderSidebar({
      photos: [
        { id: "1", filename: "trip/a.jpg", created_at: "2023-07-04T12:00:00" },
        { id: "2", filename: "trip/day2/b.jpg", created_at: "2023-07-05T12:00:00" },
        { id: "3", filename: "c.jpg", created_at: "2019-01-01T12:00:00" },
      ],
    });

    expect(screen.getByText("All Images")).toHaveAttribute("data-count", "3");

    fireEvent.click(screen.getByRole("button", { name: /^by folder$/i }));
    expect(screen.getByText("trip").closest(".directory-tree-item")).toHaveAttribute("data-count", "2");
    expect(screen.getByText("(Root)").closest(".directory-tree-item")).toHaveAttribute("data-count", "1");

    fireEvent.click(screen.getByRole("button", { name: /^by date$/i }));
    expect(screen.getByText("2023").closest(".directory-tree-item")).toHaveAttribute("data-count", "2");
  });

  it("shows an initial in place of the avatar for an individual with no tagged face", () => {
    renderSidebar({ individuals: [{ id: "p9", name: "zoe", has_representative_thumbnail: false }] });
    fireEvent.click(screen.getByRole("button", { name: /^by individual$/i }));

    expect(document.querySelector(".sidebar-individual-initial")).toHaveTextContent("Z");
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
    searchResultCount: null,
    searchActive: false,
    onSearchResults: vi.fn(),
    ...overrides,
  };
}
