import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PhotoCard from "./PhotoCard.jsx";

const base = { id: "1", filename: "a.jpg", media_kind: "image" };

describe("PhotoCard motion badge", () => {
  it("shows nothing on a still", () => {
    render(<PhotoCard photo={base} onSelect={() => {}} />);
    expect(screen.queryByRole("img", { name: /video|animated/i })).not.toBeInTheDocument();
  });

  it("shows a video-camera badge with the length on a video", () => {
    render(
      <PhotoCard photo={{ ...base, filename: "a.mp4", media_kind: "video", duration_seconds: 42 }} onSelect={() => {}} />
    );
    const badge = screen.getByRole("img", { name: "Video, 0:42" });
    expect(badge).toHaveClass("photo-card-motion");
    expect(badge).toHaveTextContent("0:42");
  });

  it("shows the badge without a length when the duration is unknown", () => {
    render(<PhotoCard photo={{ ...base, media_kind: "video", duration_seconds: null }} onSelect={() => {}} />);
    expect(screen.getByRole("img", { name: "Video" })).toBeInTheDocument();
  });

  it("badges an animated image, without a length", () => {
    render(<PhotoCard photo={{ ...base, media_kind: "animated" }} onSelect={() => {}} />);
    const badge = screen.getByRole("img", { name: "Animated image" });
    expect(badge).not.toHaveTextContent(/\d/);
  });

  it("keeps the search score alongside the badge", () => {
    const { container } = render(
      <PhotoCard photo={{ ...base, media_kind: "video", duration_seconds: 5, search_score: 0.87 }} onSelect={() => {}} />
    );
    expect(screen.getByText("87%")).toBeInTheDocument();
    expect(container.querySelector(".photo-card")).toHaveClass("photo-card-moving", "photo-card-scored");
  });

  it("still finds the tile by the thumbnail's alt text", () => {
    render(<PhotoCard photo={{ ...base, filename: "clip.mp4", media_kind: "video" }} onSelect={() => {}} />);
    expect(screen.getByAltText("clip.mp4")).toBeInTheDocument();
  });
});
