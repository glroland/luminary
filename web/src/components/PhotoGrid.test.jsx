import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client.js";
import PhotoGrid, { BATCH_SIZE } from "./PhotoGrid.jsx";

vi.mock("../api/client.js", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchTags: vi.fn(),
  fetchPhotoTags: vi.fn(),
  tagPhoto: vi.fn(),
  untagPhoto: vi.fn(),
}));

const PHOTOS = [
  { id: "1", filename: "beach.jpg" },
  { id: "2", filename: "mountain.jpg" },
];

function rightClick(element) {
  fireEvent.contextMenu(element, { clientX: 50, clientY: 60 });
}

describe("PhotoGrid", () => {
  beforeEach(() => {
    client.fetchTags.mockResolvedValue([
      { id: "tag-1", name: "Vacation" },
      { id: "tag-2", name: "Pets" },
    ]);
    client.fetchPhotoTags.mockResolvedValue([]);
    client.tagPhoto.mockResolvedValue();
    client.untagPhoto.mockResolvedValue();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the empty message when there are no photos", () => {
    render(<PhotoGrid photos={[]} onSelect={() => {}} emptyMessage="Nothing here." />);
    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
  });

  it("selects a photo when its thumbnail is clicked", () => {
    const onSelect = vi.fn();
    render(<PhotoGrid photos={PHOTOS} onSelect={onSelect} />);

    fireEvent.click(screen.getByAltText("beach.jpg"));

    // Second argument: the clicked tile's centre, for the modal's open animation.
    expect(onSelect).toHaveBeenCalledWith(PHOTOS[0], { x: expect.any(Number), y: expect.any(Number) });
  });

  it("opens a tag menu on right-click, fetching the catalog and the photo's current tags", async () => {
    client.fetchPhotoTags.mockResolvedValue([{ id: "tag-1", name: "Vacation" }]);
    render(<PhotoGrid photos={PHOTOS} onSelect={() => {}} />);

    rightClick(screen.getByAltText("beach.jpg").closest(".photo-card"));

    expect(client.fetchTags).toHaveBeenCalled();
    expect(client.fetchPhotoTags).toHaveBeenCalledWith("1");

    const menu = await waitFor(() => document.querySelector(".tag-menu"));
    await waitFor(() => expect(within(menu).getByText("Vacation")).toBeInTheDocument());
    expect(within(menu).getByText("Pets")).toBeInTheDocument();
    // Already-applied tags show a checkmark.
    const vacationItem = within(menu).getByText("Vacation").closest(".tag-menu-item");
    expect(vacationItem).toHaveClass("tag-menu-item-checked");
    const petsItem = within(menu).getByText("Pets").closest(".tag-menu-item");
    expect(petsItem).not.toHaveClass("tag-menu-item-checked");
  });

  it("tags a photo by clicking an unchecked catalog tag, and the menu stays open", async () => {
    render(<PhotoGrid photos={PHOTOS} onSelect={() => {}} />);

    rightClick(screen.getByAltText("beach.jpg").closest(".photo-card"));
    const menu = await waitFor(() => document.querySelector(".tag-menu"));
    await waitFor(() => expect(within(menu).getByText("Vacation")).toBeInTheDocument());

    fireEvent.click(within(menu).getByText("Vacation"));

    await waitFor(() => expect(client.tagPhoto).toHaveBeenCalledWith("1", "tag-1"));
    // Still open, and the clicked item now shows as checked.
    expect(document.querySelector(".tag-menu")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(menu).getByText("Vacation").closest(".tag-menu-item")).toHaveClass("tag-menu-item-checked")
    );
  });

  it("untags a photo by clicking an already-checked catalog tag", async () => {
    client.fetchPhotoTags.mockResolvedValue([{ id: "tag-1", name: "Vacation" }]);
    render(<PhotoGrid photos={PHOTOS} onSelect={() => {}} />);

    rightClick(screen.getByAltText("beach.jpg").closest(".photo-card"));
    const menu = await waitFor(() => document.querySelector(".tag-menu"));
    await waitFor(() =>
      expect(within(menu).getByText("Vacation").closest(".tag-menu-item")).toHaveClass("tag-menu-item-checked")
    );

    fireEvent.click(within(menu).getByText("Vacation"));

    await waitFor(() => expect(client.untagPhoto).toHaveBeenCalledWith("1", "tag-1"));
    await waitFor(() =>
      expect(within(menu).getByText("Vacation").closest(".tag-menu-item")).not.toHaveClass("tag-menu-item-checked")
    );
  });

  it("closes the tag menu on an outside click", async () => {
    render(<PhotoGrid photos={PHOTOS} onSelect={() => {}} />);

    rightClick(screen.getByAltText("beach.jpg").closest(".photo-card"));
    await waitFor(() => expect(document.querySelector(".tag-menu")).toBeInTheDocument());

    fireEvent.click(document.body);

    expect(document.querySelector(".tag-menu")).not.toBeInTheDocument();
  });

  it("closes the tag menu on Escape", async () => {
    render(<PhotoGrid photos={PHOTOS} onSelect={() => {}} />);

    rightClick(screen.getByAltText("beach.jpg").closest(".photo-card"));
    await waitFor(() => expect(document.querySelector(".tag-menu")).toBeInTheDocument());

    fireEvent.keyDown(window, { key: "Escape" });

    expect(document.querySelector(".tag-menu")).not.toBeInTheDocument();
  });

  it("suppresses the browser's native context menu on a photo card", async () => {
    render(<PhotoGrid photos={PHOTOS} onSelect={() => {}} />);

    const event = fireEvent.contextMenu(screen.getByAltText("beach.jpg").closest(".photo-card"));

    expect(event).toBe(false); // fireEvent returns false when preventDefault() was called
    // The right-click also kicks off the catalog/photo-tags fetches above --
    // flush those before the test ends so their resolution isn't a
    // state update outside of act().
    await act(async () => {});
  });

  it("switches the menu to a different photo on a second right-click without closing first", async () => {
    render(<PhotoGrid photos={PHOTOS} onSelect={() => {}} />);

    rightClick(screen.getByAltText("beach.jpg").closest(".photo-card"));
    await waitFor(() => expect(client.fetchPhotoTags).toHaveBeenCalledWith("1"));

    rightClick(screen.getByAltText("mountain.jpg").closest(".photo-card"));

    await waitFor(() => expect(client.fetchPhotoTags).toHaveBeenCalledWith("2"));
    expect(document.querySelector(".tag-menu")).toBeInTheDocument();
  });

  it("renders no filename caption, and puts the description in a tooltip", () => {
    render(
      <PhotoGrid
        photos={[{ id: "1", filename: "beach.jpg", description: "A sandy beach at sunset." }, PHOTOS[1]]}
        onSelect={() => {}}
      />
    );

    expect(screen.queryByText("beach.jpg")).not.toBeInTheDocument();
    expect(screen.getByRole("tooltip")).toHaveTextContent("A sandy beach at sunset.");
    // Only the photo that has a description gets one.
    expect(screen.getAllByRole("tooltip")).toHaveLength(1);
  });

  it("shows the date and location in each tile's hover caption", () => {
    render(
      <PhotoGrid
        photos={[
          { id: "1", filename: "beach.jpg", created_at: "2019-03-10T12:00:00", location_name: "Boone, North Carolina" },
        ]}
        onSelect={() => {}}
      />
    );

    const overlay = document.querySelector(".photo-card-overlay");
    expect(overlay).toHaveTextContent(/2019/);
    expect(overlay).toHaveTextContent("Boone, North Carolina");
  });

  it("marks a tile loaded once its thumbnail arrives", () => {
    render(<PhotoGrid photos={PHOTOS} onSelect={() => {}} />);
    const img = screen.getByAltText("beach.jpg");
    expect(img.closest(".photo-card")).not.toHaveClass("photo-card-loaded");

    fireEvent.load(img);

    expect(img.closest(".photo-card")).toHaveClass("photo-card-loaded");
  });

  it("groups consecutive photos into sections with a header and a count", () => {
    render(
      <PhotoGrid
        groupBy="folder"
        photos={[
          { id: "1", filename: "trip/a.jpg" },
          { id: "2", filename: "trip/b.jpg" },
          { id: "3", filename: "c.jpg" },
        ]}
        onSelect={() => {}}
      />
    );

    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings.map((heading) => heading.textContent)).toEqual(["trip2", "(Root)1"]);
    expect(within(headings[0].closest("section")).getAllByRole("img")).toHaveLength(2);
  });

  describe("infinite scroll", () => {
    let observers;

    beforeEach(() => {
      observers = [];
      vi.stubGlobal(
        "IntersectionObserver",
        class {
          constructor(callback) {
            this.callback = callback;
            this.disconnected = false;
            observers.push(this);
          }
          observe() {}
          disconnect() {
            this.disconnected = true;
          }
        }
      );
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    function scrollToBottom() {
      const live = observers.filter((observer) => !observer.disconnected);
      act(() => live.forEach((observer) => observer.callback([{ isIntersecting: true }])));
    }

    const many = Array.from({ length: BATCH_SIZE + 5 }, (_, index) => ({
      id: String(index),
      filename: `photo-${index}.jpg`,
    }));

    it("renders one batch, then the rest when the bottom scrolls into view", () => {
      render(<PhotoGrid photos={many} onSelect={() => {}} />);
      expect(screen.getAllByRole("img")).toHaveLength(BATCH_SIZE);
      expect(screen.queryByAltText(`photo-${BATCH_SIZE}.jpg`)).not.toBeInTheDocument();

      scrollToBottom();

      expect(screen.getAllByRole("img")).toHaveLength(BATCH_SIZE + 5);
      // Nothing left to load: the sentinel is gone.
      expect(document.querySelector(".photo-grid-sentinel")).not.toBeInTheDocument();
    });

    it("doesn't observe anything when the whole list fits in the first batch", () => {
      render(<PhotoGrid photos={PHOTOS} onSelect={() => {}} />);
      expect(observers).toHaveLength(0);
    });
  });
});
