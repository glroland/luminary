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
  fetchPhotoTags: vi.fn(),
  fetchTags: vi.fn(),
  tagPhoto: vi.fn(),
  untagPhoto: vi.fn(),
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
    client.fetchPhotoTags.mockResolvedValue([]);
    client.fetchTags.mockResolvedValue([]);
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

    expect(screen.getByRole("heading", { name: "beach.jpg" })).toBeInTheDocument();
    expect(screen.getByText("1000 × 800")).toBeInTheDocument();

    const downloadLink = screen.getByRole("link", { name: /download/i });
    expect(downloadLink).toHaveAttribute("href", expect.stringContaining("/photos/1/original"));
    expect(downloadLink).toHaveAttribute("download", "beach.jpg");

    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the full relative path as the heading and names the download after the basename", async () => {
    render(<PhotoModal photo={{ ...PHOTO, filename: "vacation/2019/beach.jpg" }} onClose={() => {}} />);
    await flush();

    expect(screen.getByRole("heading", { name: "vacation/2019/beach.jpg" })).toBeInTheDocument();

    const downloadLink = screen.getByRole("link", { name: /download/i });
    expect(downloadLink).toHaveAttribute("download", "beach.jpg");
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

  it("renders the reverse-geocoded location name when present", async () => {
    render(<PhotoModal photo={{ ...PHOTO, location_name: "Boone, North Carolina" }} onClose={() => {}} />);
    await flush();
    expect(screen.getByText("Boone, North Carolina")).toBeInTheDocument();
  });

  it("renders no location paragraph when absent", async () => {
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    await flush();
    expect(document.querySelector(".photo-modal-location")).not.toBeInTheDocument();
  });

  it("positions a box overlay in pixels from the image's measured visible rect, accounting for object-fit: contain letterboxing", async () => {
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    await flush();

    const img = document.querySelector(".photo-modal-image-pane img");

    // Simulate the <img> element laid out as a 600x600 box (object-fit:
    // contain will letterbox it, since the 1000x800 photo's aspect ratio
    // -- 1.25 -- doesn't match the square element). Visible image content
    // should work out to 600x480, vertically centered (top offset 60px).
    // offsetWidth/offsetHeight/offsetLeft/offsetTop, not
    // getBoundingClientRect(), since those are what measureVisibleImageRect
    // now reads -- see its own docstring for why.
    setOffsetRect(img, { left: 0, top: 0, width: 600, height: 600 });
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

  // jsdom has no real layout engine -- offsetWidth/offsetHeight/offsetLeft/
  // offsetTop are plain getters that return 0 unless overridden per
  // instance, same as naturalWidth/naturalHeight below. Mirrors
  // measureVisibleImageRect's own use of these (not getBoundingClientRect())
  // -- see that function's docstring for why.
  function setOffsetRect(el, { left, top, width, height }) {
    Object.defineProperty(el, "offsetLeft", { value: left, configurable: true });
    Object.defineProperty(el, "offsetTop", { value: top, configurable: true });
    Object.defineProperty(el, "offsetWidth", { value: width, configurable: true });
    Object.defineProperty(el, "offsetHeight", { value: height, configurable: true });
  }

  function measureImage() {
    const img = document.querySelector(".photo-modal-image-pane img");
    setOffsetRect(img, { left: 0, top: 0, width: 1000, height: 800 });
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
    // No detection_confidence on this box -- the score badge is omitted
    // entirely rather than showing e.g. "NaN%".
    expect(document.querySelector(".photo-modal-bbox-score")).not.toBeInTheDocument();
  });

  it("shows MTCNN's detection confidence as a rounded percentage on each face box", async () => {
    client.fetchFaces.mockResolvedValue([
      { id: "f1", x1: 100, y1: 100, x2: 300, y2: 300, detection_confidence: 0.873 },
    ]);
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    measureImage();

    pressF();

    await waitFor(() => expect(document.querySelector(".photo-modal-bbox-face")).toBeInTheDocument());
    expect(document.querySelector(".photo-modal-bbox-score").textContent).toBe("87%");
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

  describe("hover zoom", () => {
    it("scales the image toward the cursor on hover and resets on mouse leave", async () => {
      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      await flush();

      const img = document.querySelector(".photo-modal-image-pane img");
      vi.spyOn(img, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 200, height: 100 });

      fireEvent.mouseMove(img, { clientX: 50, clientY: 25 });
      expect(img).toHaveClass("photo-modal-image-zoomed");
      expect(img.style.transformOrigin).toBe("25% 25%");

      fireEvent.mouseLeave(img);
      expect(img).not.toHaveClass("photo-modal-image-zoomed");
    });

    it("does not zoom while a face box overlay is shown, to avoid desyncing it from the image", async () => {
      client.fetchFaces.mockResolvedValue([{ x1: 100, y1: 100, x2: 300, y2: 300 }]);
      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      measureImage();

      pressF();
      await waitFor(() => expect(document.querySelector(".photo-modal-bbox-face")).toBeInTheDocument());

      const img = document.querySelector(".photo-modal-image-pane img");
      fireEvent.mouseMove(img, { clientX: 50, clientY: 25 });
      expect(img).not.toHaveClass("photo-modal-image-zoomed");
    });

    it("zooms back out immediately when 'f' is pressed while already zoomed in", async () => {
      client.fetchFaces.mockResolvedValue([{ x1: 100, y1: 100, x2: 300, y2: 300 }]);
      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      measureImage();

      const img = document.querySelector(".photo-modal-image-pane img");
      vi.spyOn(img, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 200, height: 100 });
      fireEvent.mouseMove(img, { clientX: 50, clientY: 25 });
      expect(img).toHaveClass("photo-modal-image-zoomed");

      // The mouse hasn't moved off the image -- pressing "f" alone (not a
      // mouseleave) must still drop the zoom, since the face boxes about to
      // render are positioned for the unzoomed image.
      pressF();
      expect(img).not.toHaveClass("photo-modal-image-zoomed");
      await flush();
    });

    it("zooms back out when a chat reply brings in grounding boxes", async () => {
      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      measureImage();
      await flush();

      const img = document.querySelector(".photo-modal-image-pane img");
      vi.spyOn(img, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 200, height: 100 });
      fireEvent.mouseMove(img, { clientX: 50, clientY: 25 });
      expect(img).toHaveClass("photo-modal-image-zoomed");

      fireEvent.click(screen.getByText("trigger-boxes"));
      expect(img).not.toHaveClass("photo-modal-image-zoomed");
    });

    it("positions face boxes correctly even while getBoundingClientRect reports a zoom-distorted box", async () => {
      // Regression (two reported symptoms, same root cause): boxes landed
      // in the wrong place either (a) opening the modal with the cursor
      // already resting on the image, racing a hover-triggered zoom
      // against the image's very first rect measurement, or (b) pressing
      // "f" while already zoomed in, which only *starts* an animated
      // transition back to 1x (styles.css's `transition: transform 0.1s
      // ease-out`) rather than snapping instantly -- so even measuring
      // right as zoom "ends" could still catch it mid-transition.
      //
      // Both traced back to measureVisibleImageRect reading
      // getBoundingClientRect(), which reports the *painted* (transformed)
      // box -- scaled, or some in-between value, whenever a zoom transform
      // is active or animating. It now reads offsetWidth/offsetHeight/
      // offsetLeft/offsetTop instead, which reflect the element's layout
      // box and are never affected by `transform` at all, so there's no
      // "measured at a bad time" case left to race against. Proof: this
      // sets getBoundingClientRect to something wildly different from the
      // real (offset-based) box, and the face box still renders correctly.
      client.fetchFaces.mockResolvedValue([{ id: "f1", x1: 100, y1: 100, x2: 300, y2: 300 }]);
      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      await flush();

      const img = document.querySelector(".photo-modal-image-pane img");
      setOffsetRect(img, { left: 0, top: 0, width: 1000, height: 800 });
      Object.defineProperty(img, "naturalWidth", { value: 1000, configurable: true });
      Object.defineProperty(img, "naturalHeight", { value: 800, configurable: true });
      vi.spyOn(img, "getBoundingClientRect").mockReturnValue({ left: -500, top: -400, width: 2500, height: 2000 });

      fireEvent.load(img);
      pressF();

      const box = await waitForElement(".photo-modal-bbox-face");
      // Correct per the real 1000x800 offset-based box: x1=100/1000*1000
      // etc. -- using getBoundingClientRect's decoy value above instead
      // would have rendered this box far outside the image entirely.
      expect(box.style.left).toBe("100px");
      expect(box.style.top).toBe("100px");
      expect(box.style.width).toBe("200px");
      expect(box.style.height).toBe("200px");
    });
  });

  describe("full screen", () => {
    afterEach(() => {
      delete document.exitFullscreen;
      Object.defineProperty(document, "fullscreenElement", { value: null, configurable: true });
    });

    it("requests full screen on the modal and reflects the button label from fullscreenchange", async () => {
      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      await flush();

      const modalNode = document.querySelector(".photo-modal");
      modalNode.requestFullscreen = vi.fn(() => {
        Object.defineProperty(document, "fullscreenElement", { value: modalNode, configurable: true });
        document.dispatchEvent(new Event("fullscreenchange"));
        return Promise.resolve();
      });
      document.exitFullscreen = vi.fn(() => {
        Object.defineProperty(document, "fullscreenElement", { value: null, configurable: true });
        document.dispatchEvent(new Event("fullscreenchange"));
        return Promise.resolve();
      });

      fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
      expect(modalNode.requestFullscreen).toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Exit full screen" })).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Exit full screen" }));
      expect(document.exitFullscreen).toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Full screen" })).toBeInTheDocument();
    });
  });

  describe("tags", () => {
    it("shows an empty state when the photo has no tags", async () => {
      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      await waitFor(() => expect(screen.getByText("No tags on this photo yet.")).toBeInTheDocument());
    });

    it("lists the photo's current tags", async () => {
      client.fetchPhotoTags.mockResolvedValue([{ id: "tag-1", name: "Vacation" }]);

      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);

      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());
    });

    it("adds an existing catalog tag to the photo via the dropdown", async () => {
      client.fetchPhotoTags.mockResolvedValue([]);
      client.fetchTags.mockResolvedValue([{ id: "tag-1", name: "Vacation" }]);
      client.tagPhoto.mockResolvedValue();

      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      await waitFor(() => expect(screen.getByText("No tags on this photo yet.")).toBeInTheDocument());

      fireEvent.change(screen.getByRole("combobox"), { target: { value: "tag-1" } });
      fireEvent.click(screen.getByRole("button", { name: "Add" }));

      await waitFor(() => expect(client.tagPhoto).toHaveBeenCalledWith("1", "tag-1"));
      await waitFor(() => expect(screen.queryByText("No tags on this photo yet.")).not.toBeInTheDocument());
      expect(screen.getByText("Vacation")).toBeInTheDocument();
    });

    it("does not offer a tag that's already applied to the photo", async () => {
      client.fetchPhotoTags.mockResolvedValue([{ id: "tag-1", name: "Vacation" }]);
      client.fetchTags.mockResolvedValue([
        { id: "tag-1", name: "Vacation" },
        { id: "tag-2", name: "Pets" },
      ]);

      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());

      const select = await waitFor(() => screen.getByRole("combobox"));
      const optionNames = within(select)
        .getAllByRole("option")
        .map((option) => option.textContent);
      expect(optionNames).not.toContain("Vacation");
      expect(optionNames).toContain("Pets");
    });

    it("removes a tag from the photo", async () => {
      client.fetchPhotoTags.mockResolvedValue([{ id: "tag-1", name: "Vacation" }]);
      client.untagPhoto.mockResolvedValue();

      render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "Remove tag Vacation" }));

      expect(client.untagPhoto).toHaveBeenCalledWith("1", "tag-1");
      await waitFor(() => expect(screen.queryByText("Vacation")).not.toBeInTheDocument());
      expect(screen.getByText("No tags on this photo yet.")).toBeInTheDocument();
    });

    it("resets tags when the photo changes", async () => {
      client.fetchPhotoTags.mockResolvedValue([{ id: "tag-1", name: "Vacation" }]);

      const { rerender } = render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());

      client.fetchPhotoTags.mockResolvedValue([]);
      rerender(<PhotoModal photo={{ ...PHOTO, id: "2" }} onClose={() => {}} />);

      await waitFor(() => expect(screen.getByText("No tags on this photo yet.")).toBeInTheDocument());
      expect(screen.queryByText("Vacation")).not.toBeInTheDocument();
    });
  });
});
