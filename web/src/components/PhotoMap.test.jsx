import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PhotoMap from "./PhotoMap.jsx";

describe("PhotoMap", () => {
  it("shows a placeholder when the photo has no location data", () => {
    render(<PhotoMap latitude={null} longitude={null} />);
    expect(screen.getByText(/no location data/i)).toBeInTheDocument();
  });

  it("renders a map container when location data is present", () => {
    const { container } = render(<PhotoMap latitude={40.44} longitude={-79.95} />);
    expect(container.querySelector(".photo-map")).toBeInTheDocument();
    expect(screen.queryByText(/no location data/i)).not.toBeInTheDocument();
  });
});
