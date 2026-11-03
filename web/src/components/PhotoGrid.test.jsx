import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client.js";
import PhotoGrid from "./PhotoGrid.jsx";

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

    expect(onSelect).toHaveBeenCalledWith(PHOTOS[0]);
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
});
