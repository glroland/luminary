import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import * as client from "./api/client.js";

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(client, "fetchSyncStatus").mockResolvedValue({ status: "idle" });
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
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

    // Refresh now lives on the Manage page (see ManagementPage.jsx) --
    // navigate there first, same as a real user would.
    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    fireEvent.click(screen.getByText("Manage"));

    // Optimistic progress UI appears immediately, before the first poll.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^refresh$/i }));
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/syncing 40%/i)).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/done/i)).toBeInTheDocument();
    expect(client.fetchPhotos).toHaveBeenCalledTimes(2);

    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByRole("button", { name: /^refresh$/i })).toBeInTheDocument();
  });

  it("narrows the grid to an individual's tagged photos when selected in the sidebar", async () => {
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([
      { id: "1", filename: "beach.jpg", width: 100, height: 100, created_at: new Date().toISOString() },
      { id: "2", filename: "party.jpg", width: 100, height: 100, created_at: new Date().toISOString() },
    ]);
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([
      { id: "p1", name: "Alice", has_representative_thumbnail: false },
    ]);
    vi.spyOn(client, "fetchIndividualFaces").mockResolvedValue([{ id: "f1", photo_id: "1", tag_source: "manual" }]);

    render(<App />);
    await waitFor(() => expect(screen.getByText("beach.jpg")).toBeInTheDocument());
    expect(screen.getByText("party.jpg")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    fireEvent.click(screen.getByText("Alice"));

    await waitFor(() => expect(screen.queryByText("party.jpg")).not.toBeInTheDocument());
    expect(screen.getByText("beach.jpg")).toBeInTheDocument();

    // "All Images" clears the filter again.
    fireEvent.click(screen.getByText("All Images"));
    await waitFor(() => expect(screen.getByText("party.jpg")).toBeInTheDocument());
  });

  it("paginates the grid and lets the user change the page size", async () => {
    const photos = Array.from({ length: 30 }, (_, index) => ({
      id: String(index),
      filename: `photo-${String(index).padStart(2, "0")}.jpg`,
      width: 100,
      height: 100,
      created_at: new Date().toISOString(),
    }));
    vi.spyOn(client, "fetchPhotos").mockResolvedValue(photos);

    render(<App />);
    await waitFor(() => expect(screen.getByText("photo-00.jpg")).toBeInTheDocument());

    // Default page size (15) shows the first 15 photos and hides the rest.
    expect(screen.getByText("photo-14.jpg")).toBeInTheDocument();
    expect(screen.queryByText("photo-15.jpg")).not.toBeInTheDocument();
    expect(screen.getByText(/page 1 of 2/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await waitFor(() => expect(screen.getByText("photo-15.jpg")).toBeInTheDocument());
    expect(screen.queryByText("photo-00.jpg")).not.toBeInTheDocument();
    expect(screen.getByText(/page 2 of 2/i)).toBeInTheDocument();

    // Switching the page size recomputes the page count and jumps back to page 1.
    fireEvent.change(screen.getByLabelText(/per page/i), { target: { value: "200" } });
    await waitFor(() => expect(screen.getByText("photo-00.jpg")).toBeInTheDocument());
    expect(screen.getByText("photo-29.jpg")).toBeInTheDocument();
    expect(screen.getByText(/page 1 of 1/i)).toBeInTheDocument();
  });

  it("searches, narrows the grid by similarity, then clears via the sidebar's x button", async () => {
    const createdAt = new Date().toISOString();
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([
      { id: "1", filename: "beach.jpg", width: 100, height: 100, created_at: createdAt },
      { id: "2", filename: "party.jpg", width: 100, height: 100, created_at: createdAt },
    ]);
    const searchMock = vi.spyOn(client, "searchPhotos").mockResolvedValue([
      { id: "2", filename: "party.jpg", width: 100, height: 100, created_at: createdAt, similarity: 0.9 },
    ]);

    render(<App />);
    await waitFor(() => expect(screen.getByText("beach.jpg")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    fireEvent.change(screen.getByPlaceholderText(/search photo descriptions/i), {
      target: { value: "a party" },
    });

    // The search section's own submit button shares its accessible name
    // ("Search") with the collapsible section header above it -- find it
    // by its distinguishing type="submit" instead of by role+name, same
    // disambiguation Sidebar.test.jsx uses.
    const submitButton = document.querySelector(".sidebar-search-actions button[type='submit']");
    await act(async () => {
      fireEvent.click(submitButton);
      await Promise.resolve();
    });

    expect(searchMock).toHaveBeenCalledWith("a party", 0.5); // 0.5 = the default ("Moderate match") threshold
    await waitFor(() => expect(screen.queryByText("beach.jpg")).not.toBeInTheDocument());
    expect(screen.getByText("party.jpg")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /clear search/i }));
    await waitFor(() => expect(screen.getByText("beach.jpg")).toBeInTheDocument());
    expect(screen.getByText("party.jpg")).toBeInTheDocument();
  });

  it("clears an active search when a different sidebar filter is selected", async () => {
    const createdAt = new Date().toISOString();
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([
      { id: "1", filename: "beach.jpg", width: 100, height: 100, created_at: createdAt },
      { id: "2", filename: "party.jpg", width: 100, height: 100, created_at: createdAt },
    ]);
    vi.spyOn(client, "searchPhotos").mockResolvedValue([
      { id: "2", filename: "party.jpg", width: 100, height: 100, created_at: createdAt, similarity: 0.9 },
    ]);

    render(<App />);
    await waitFor(() => expect(screen.getByText("beach.jpg")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    fireEvent.change(screen.getByPlaceholderText(/search photo descriptions/i), {
      target: { value: "a party" },
    });
    const submitButton = document.querySelector(".sidebar-search-actions button[type='submit']");
    await act(async () => {
      fireEvent.click(submitButton);
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.queryByText("beach.jpg")).not.toBeInTheDocument());

    fireEvent.click(screen.getByText("All Images"));
    await waitFor(() => expect(screen.getByText("beach.jpg")).toBeInTheDocument());
    expect(screen.getByText("party.jpg")).toBeInTheDocument();
    // The clear ("x") button only shows while a search is active.
    expect(screen.queryByRole("button", { name: /clear search/i })).not.toBeInTheDocument();
  });
});
