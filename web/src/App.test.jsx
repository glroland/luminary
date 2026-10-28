import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import * as client from "./api/client.js";

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(client, "fetchSyncStatus").mockResolvedValue({ status: "idle" });
  });

  afterEach(() => {
    vi.useRealTimers();
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
        created_at: new Date().toISOString(),
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

  it("starts a sync from the Refresh button and reflects progress through to completion", async () => {
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([]);
    vi.spyOn(client, "startSync").mockResolvedValue({ status: "idle" });

    const fetchStatusMock = vi.spyOn(client, "fetchSyncStatus");
    fetchStatusMock.mockResolvedValueOnce({ status: "idle" }); // initial mount check
    fetchStatusMock.mockResolvedValueOnce({ status: "running", processed: 4, total: 10 });
    fetchStatusMock.mockResolvedValue({
      status: "completed",
      processed: 10,
      total: 10,
      inserted: 10,
      updated: 0,
      deleted: 0,
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/no photos yet/i)).toBeInTheDocument();
    });

    vi.useFakeTimers({ shouldAdvanceTime: true });

    // Optimistic progress UI appears immediately, before the first poll.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/syncing 40%/i)).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/done/i)).toBeInTheDocument();
    expect(client.fetchPhotos).toHaveBeenCalledTimes(2);

    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByRole("button", { name: /refresh/i })).toBeInTheDocument();
  });
});
