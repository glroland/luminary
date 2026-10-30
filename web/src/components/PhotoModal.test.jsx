import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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
  it("renders nothing when there's no photo", () => {
    const { container } = render(<PhotoModal photo={null} onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders photo metadata and calls onClose", () => {
    const onClose = vi.fn();
    render(<PhotoModal photo={PHOTO} onClose={onClose} />);

    expect(screen.getByText("beach.jpg")).toBeInTheDocument();
    expect(screen.getByText("1000 × 800")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("renders the florence-generated description when present", () => {
    render(<PhotoModal photo={{ ...PHOTO, description: "A sandy beach at sunset." }} onClose={() => {}} />);
    expect(screen.getByText("A sandy beach at sunset.")).toBeInTheDocument();
  });

  it("renders no description paragraph when absent", () => {
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);
    expect(document.querySelector(".photo-modal-description")).not.toBeInTheDocument();
  });

  it("positions a box overlay in pixels from the image's measured visible rect, accounting for object-fit: contain letterboxing", () => {
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);

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

  it("does not render a box overlay before the image has been measured", () => {
    render(<PhotoModal photo={PHOTO} onClose={() => {}} />);

    fireEvent.click(screen.getByText("trigger-boxes"));

    expect(document.querySelector(".photo-modal-bbox")).not.toBeInTheDocument();
  });
});
