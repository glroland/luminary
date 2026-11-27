import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import VideoPlayer from "./VideoPlayer.jsx";

const PHOTO = { id: "7", filename: "trip/beach.mov", media_kind: "video", content_type: "video/quicktime" };

describe("VideoPlayer", () => {
  it("streams the original into a native player with the poster", () => {
    const { container } = render(<VideoPlayer photo={PHOTO} />);
    const player = container.querySelector("video");
    expect(player).toHaveAttribute("src", expect.stringContaining("/photos/7/original"));
    expect(player).toHaveAttribute("poster", expect.stringContaining("/photos/7/thumbnail"));
    expect(player.controls).toBe(true);
  });

  it("reports ready once the metadata has loaded", () => {
    const onReady = vi.fn();
    const { container } = render(<VideoPlayer photo={PHOTO} onReady={onReady} />);
    fireEvent.loadedMetadata(container.querySelector("video"));
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it("falls back to the poster and a download link when the browser can't play it", () => {
    const onReady = vi.fn();
    const { container } = render(<VideoPlayer photo={PHOTO} onReady={onReady} />);
    fireEvent.error(container.querySelector("video"));

    expect(container.querySelector("video")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/can.t play this video format/i);
    const link = screen.getByRole("link", { name: /download the original/i });
    expect(link).toHaveAttribute("href", expect.stringContaining("/photos/7/original"));
    expect(link).toHaveAttribute("download", "beach.mov");
    expect(onReady).toHaveBeenCalled(); // stops the backend-status polling
  });

  it("tries again for the next video", () => {
    const { container, rerender } = render(<VideoPlayer photo={PHOTO} />);
    fireEvent.error(container.querySelector("video"));
    rerender(<VideoPlayer photo={{ ...PHOTO, id: "8" }} />);
    expect(container.querySelector("video")).toHaveAttribute("src", expect.stringContaining("/photos/8/original"));
  });
});
