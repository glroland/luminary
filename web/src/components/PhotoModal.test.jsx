import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client.js";
import PhotoModal from "./PhotoModal.jsx";

vi.mock("./PhotoMap.jsx", () => ({ default: () => <div data-testid="photo-map" /> }));
vi.mock("./PhotoChat.jsx", () => ({
  // Exposes onBoxesChange via a button so tests can trigger it directly,
  // without pulling in the real chat flow/network calls.
  default: ({ onBoxesChange }) => (
    <button onClick={() => onBoxesChange([{ label: "eye", x1: 100, y1: 200, x2: 300, y2: 400 }])}>
      trigger-boxes
    </button>
  ),
}));
vi.mock("../api/client.js", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchFaces: vi.fn(),
  fetchIndividuals: vi.fn(),
  createIndividual: vi.fn(),
  tagFace: vi.fn(),
}));

function pressF() {
  fireEvent.keyDown(window, { key: "f" });
}

// waitFor only retries while its callback throws -- document.querySelector
// returning null doesn't throw, so a plain `waitFor(() => document.query
// Selector(...))` would resolve immediately with null instead of waiting.
// This wraps that in an assertion that does throw until the element shows up.
async function waitForElement(selector) {
  await waitFor(() => expect(document.querySelector(selector)).toBeInTheDocument());
  return document.querySelector(selector);
}

// PhotoModal now fetches faces/individuals eagerly as soon as a photo
// opens (see its "People in this photo" panel), not just when "f" is
// pressed -- tests that don't otherwise await anything need to flush that
// resolution before finishing, or React logs an "update not wrapped in
// act" warning for a state update landing after the test's assertions.
async function flush() {
  await act(async () => {});
}

const PHOTO = {
  id: "1",
  filename: "beach.jpg",
  width: 1000,
  height: 800,
  created_at: "2024-01-01T00:00:00Z",
  latitude: null,
  longitude: null,
};

describe("PhotoModal", () => {
  beforeEach(() => {
    // Both are now fetched eagerly as soon as a photo opens (see
    // PhotoModal.jsx's "People in this photo" panel), not just when "f"
    // is pressed -- default them for tests that don't care about faces/
    // individuals; tests that do override these themselves.
    client.fetchFaces.mockResolvedValue([]);
    client.fetchIndividuals.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when there's no photo", () => {
    const { container } = render(<PhotoModal photo={null} onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders photo metadata and calls onClose", async () => {
    const onClose = vi.fn();
    render(<PhotoModal photo={PHOTO} onClose={onClose} />);
    await flush();

    expect(screen.getByText("beach.jpg")).toBeInTheDocument();
    expect(screen.getByText("1000 × 800")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("renders the florence-generated description when present", async () => {
    render(<PhotoModal photo={{ ...PHOTO, description: "A sandy beach at sunset." }} onClose={() => {}} />);
    await flush();
    expect(screen.getByText("A sandy beach at sunset.")).toBeInTheDocument();
  });

  it("renders no description paragraph when absent", async () => {
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    await flush();
    expect(document.querySelector(".photo-modal-description")).not.toBeInTheDocument();
  });

  it("positions a box overlay in pixels from the image's measured visible rect, accounting for object-fit: contain letterboxing", async () => {
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    await flush();

    const img = document.querySelector(".photo-modal-image-pane img");
    const wrap = document.querySelector(".photo-modal-image-wrap");

    // Simulate the <img> element rendered as a 600x600 box (object-fit:
    // contain will letterbox it, since the 1000x800 photo's aspect ratio
    // -- 1.25 -- doesn't match the square element). Visible image content
    // should work out to 600x480, vertically centered (top offset 60px).
    vi.spyOn(wrap, "getBoundingClientRect").mockReturnValue({ left: 10, top: 20, width: 600, height: 600 });
    vi.spyOn(img, "getBoundingClientRect").mockReturnValue({ left: 10, top: 20, width: 600, height: 600 });
    Object.defineProperty(img, "naturalWidth", { value: 1000, configurable: true });
    Object.defineProperty(img, "naturalHeight", { value: 800, configurable: true });

    fireEvent.load(img);
    fireEvent.click(screen.getByText("trigger-boxes"));

    const box = document.querySelector(".photo-modal-bbox");
    expect(box).toBeInTheDocument();
    // left = visibleRect.left (0) + (100/1000)*600; top = visibleRect.top
    // (60, the letterbox offset) + (200/800)*480.
    expect(box.style.left).toBe("60px");
    expect(box.style.top).toBe("180px");
    expect(box.style.width).toBe("120px");
    expect(box.style.height).toBe("120px");
    expect(screen.getByText("eye")).toBeInTheDocument();
  });

  it("does not render a box overlay before the image has been measured", async () => {
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    await flush();

    fireEvent.click(screen.getByText("trigger-boxes"));

    expect(document.querySelector(".photo-modal-bbox")).not.toBeInTheDocument();
  });

  function measureImage() {
    const img = document.querySelector(".photo-modal-image-pane img");
    const wrap = document.querySelector(".photo-modal-image-wrap");
    vi.spyOn(wrap, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 1000, height: 800 });
    vi.spyOn(img, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 1000, height: 800 });
    Object.defineProperty(img, "naturalWidth", { value: 1000, configurable: true });
    Object.defineProperty(img, "naturalHeight", { value: 800, configurable: true });
    fireEvent.load(img);
  }

  it("fetches and shows stored face boxes when 'f' is pressed", async () => {
    client.fetchFaces.mockResolvedValue([{ x1: 100, y1: 100, x2: 300, y2: 300 }]);
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    measureImage();

    pressF();

    expect(screen.getByText("Loading faces…")).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".photo-modal-bbox-face")).toBeInTheDocument());
    expect(client.fetchFaces).toHaveBeenCalledWith("1");
    expect(screen.getByText("1 face detected (press F to hide)")).toBeInTheDocument();
    // Face boxes are unlabeled, unlike the chat-grounding boxes.
    expect(document.querySelector(".photo-modal-bbox-face .photo-modal-bbox-label")).not.toBeInTheDocument();
  });

  it("hides faces on a second press without re-fetching", async () => {
    client.fetchFaces.mockResolvedValue([{ x1: 100, y1: 100, x2: 300, y2: 300 }]);
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    measureImage();

    pressF();
    await waitFor(() => expect(document.querySelector(".photo-modal-bbox-face")).toBeInTheDocument());

    pressF();
    expect(document.querySelector(".photo-modal-bbox-face")).not.toBeInTheDocument();
    expect(document.querySelector(".photo-modal-faces-hint")).not.toBeInTheDocument();

    pressF();
    expect(document.querySelector(".photo-modal-bbox-face")).toBeInTheDocument();
    expect(client.fetchFaces).toHaveBeenCalledTimes(1);
  });

  it("shows a message when no faces were detected", async () => {
    client.fetchFaces.mockResolvedValue([]);
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    measureImage();

    pressF();

    await waitFor(() => expect(screen.getByText("No faces detected for this photo.")).toBeInTheDocument());
  });

  it("ignores 'f' while typing in an editable field", async () => {
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    await flush();

    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "f" });
    document.body.removeChild(input);

    expect(document.querySelector(".photo-modal-faces-hint")).not.toBeInTheDocument();
  });

  it("resets the faces overlay when the photo changes", async () => {
    client.fetchFaces.mockResolvedValue([{ x1: 100, y1: 100, x2: 300, y2: 300 }]);
    const { rerender } = render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    measureImage();

    pressF();
    await waitFor(() => expect(document.querySelector(".photo-modal-bbox-face")).toBeInTheDocument());

    rerender(<PhotoModal photo={{ ...PHOTO, id: "2" }} onClose={() => {}} />);
    await flush();

    expect(document.querySelector(".photo-modal-bbox-face")).not.toBeInTheDocument();
    expect(document.querySelector(".photo-modal-faces-hint")).not.toBeInTheDocument();
  });

  it("tags a face: right-mouse-down opens a menu, hovering a name highlights it, releasing over it tags the face", async () => {
    client.fetchFaces.mockResolvedValue([{ id: "face-1", x1: 100, y1: 100, x2: 300, y2: 300, person_id: null }]);
    client.fetchIndividuals.mockResolvedValue([
      { id: "person-1", name: "Alice", has_representative_thumbnail: false, created_at: "2024-01-01T00:00:00Z" },
    ]);
    client.tagFace.mockResolvedValue({ id: "face-1", x1: 100, y1: 100, x2: 300, y2: 300, person_id: "person-1" });

    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    measureImage();
    pressF();

    const faceBox = await waitForElement(".photo-modal-bbox-face");
    fireEvent.mouseDown(faceBox, { button: 2, clientX: 50, clientY: 60 });

    const menu = await waitForElement(".face-tag-menu");
    const menuItem = within(menu).getByText("Alice");
    fireEvent.mouseEnter(menuItem);
    expect(menuItem).toHaveClass("face-tag-menu-item-active");

    fireEvent.mouseUp(window, { button: 2 });

    expect(document.querySelector(".face-tag-menu")).not.toBeInTheDocument();
    await waitFor(() => expect(client.tagFace).toHaveBeenCalledWith("face-1", "person-1"));

    const taggedBox = await waitForElement(".photo-modal-bbox-face-tagged");
    expect(within(taggedBox).getByText("Alice")).toBeInTheDocument();
  });

  it("closes the tag menu without tagging when released over no name", async () => {
    client.fetchFaces.mockResolvedValue([{ id: "face-1", x1: 100, y1: 100, x2: 300, y2: 300, person_id: null }]);
    client.fetchIndividuals.mockResolvedValue([
      { id: "person-1", name: "Alice", has_representative_thumbnail: false, created_at: "2024-01-01T00:00:00Z" },
    ]);

    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    measureImage();
    pressF();

    const faceBox = await waitForElement(".photo-modal-bbox-face");
    fireEvent.mouseDown(faceBox, { button: 2, clientX: 50, clientY: 60 });
    await waitFor(() => expect(document.querySelector(".face-tag-menu")).toBeInTheDocument());

    fireEvent.mouseUp(window, { button: 2 });

    expect(document.querySelector(".face-tag-menu")).not.toBeInTheDocument();
    expect(client.tagFace).not.toHaveBeenCalled();
  });

  it("ignores a left-button mouse-down on a face box (only the right button opens the tag menu)", async () => {
    client.fetchFaces.mockResolvedValue([{ id: "face-1", x1: 100, y1: 100, x2: 300, y2: 300, person_id: null }]);
    client.fetchIndividuals.mockResolvedValue([]);

    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    measureImage();
    pressF();

    const faceBox = await waitForElement(".photo-modal-bbox-face");
    fireEvent.mouseDown(faceBox, { button: 0, clientX: 50, clientY: 60 });

    expect(document.querySelector(".face-tag-menu")).not.toBeInTheDocument();
  });

  it("suppresses the browser's native context menu on a face box", async () => {
    client.fetchFaces.mockResolvedValue([{ id: "face-1", x1: 100, y1: 100, x2: 300, y2: 300, person_id: null }]);
    client.fetchIndividuals.mockResolvedValue([]);

    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    measureImage();
    pressF();

    const faceBox = await waitForElement(".photo-modal-bbox-face");
    const event = fireEvent.contextMenu(faceBox);

    expect(event).toBe(false); // fireEvent returns false when preventDefault() was called
  });

  it("adds a new individual to the catalog via the sidebar form", async () => {
    client.fetchFaces.mockResolvedValue([]);
    client.fetchIndividuals.mockResolvedValue([]);
    client.createIndividual.mockResolvedValue({
      id: "person-2",
      name: "Bob",
      has_representative_thumbnail: false,
      created_at: "2024-01-01T00:00:00Z",
    });

    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    pressF();

    await waitFor(() => expect(screen.getByText(/no individuals catalogued yet/i)).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Add a person…"), { target: { value: "Bob" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(client.createIndividual).toHaveBeenCalledWith("Bob"));
    await waitFor(() => expect(screen.getByText("Bob")).toBeInTheDocument());
    expect(screen.getByPlaceholderText("Add a person…").value).toBe("");
  });

  it("shows an empty state in 'People in this photo' before any face is tagged", async () => {
    client.fetchFaces.mockResolvedValue([{ id: "face-1", x1: 100, y1: 100, x2: 300, y2: 300, person_id: null }]);

    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText("No one identified in this photo yet.")).toBeInTheDocument());
  });

  it("lists manually and automatically tagged individuals in 'People in this photo', without pressing 'f'", async () => {
    client.fetchFaces.mockResolvedValue([
      { id: "face-1", x1: 100, y1: 100, x2: 300, y2: 300, person_id: "person-1", tag_source: "manual" },
      { id: "face-2", x1: 10, y1: 10, x2: 30, y2: 30, person_id: "person-2", tag_source: "auto" },
    ]);
    client.fetchIndividuals.mockResolvedValue([
      { id: "person-1", name: "Alice", has_representative_thumbnail: false, created_at: "2024-01-01T00:00:00Z" },
      { id: "person-2", name: "Bob", has_representative_thumbnail: false, created_at: "2024-01-01T00:00:00Z" },
    ]);

    // Deliberately never presses "f" -- this panel is always visible.
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);

    const peopleList = await waitForElement(".photo-modal-people-in-photo-list");
    expect(within(peopleList).getByText("Alice")).toBeInTheDocument();
    expect(within(peopleList).getByText("Bob")).toBeInTheDocument();
    expect(within(peopleList).getByText("auto-detected")).toBeInTheDocument();
    // Manual tags get no badge.
    expect(within(peopleList).getAllByText("auto-detected")).toHaveLength(1);
  });

  it("resets 'People in this photo' when the photo changes", async () => {
    client.fetchFaces.mockResolvedValue([
      { id: "face-1", x1: 100, y1: 100, x2: 300, y2: 300, person_id: "person-1", tag_source: "manual" },
    ]);
    client.fetchIndividuals.mockResolvedValue([
      { id: "person-1", name: "Alice", has_representative_thumbnail: false, created_at: "2024-01-01T00:00:00Z" },
    ]);

    const { rerender } = render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    client.fetchFaces.mockResolvedValue([]);
    rerender(<PhotoModal photo={{ ...PHOTO, id: "2" }} onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText("No one identified in this photo yet.")).toBeInTheDocument());
    expect(screen.queryByText("Alice")).not.toBeInTheDocument();
  });
});
