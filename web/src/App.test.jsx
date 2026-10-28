import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import * as client from "./api/client.js";

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders an empty state when there are no photos", async () => {
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([]);

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/no photos yet/i)).toBeInTheDocument();
    });
  });

  it("renders photos returned from the API", async () => {
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([
      {
        id: "1",
        filename: "beach.jpg",
        width: 100,
        height: 100,
        uploaded_at: new Date().toISOString(),
      },
    ]);

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("beach.jpg")).toBeInTheDocument();
    });
  });

  it("shows an error message when loading photos fails", async () => {
    vi.spyOn(client, "fetchPhotos").mockRejectedValue(new Error("boom"));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("boom");
    });
  });
});
