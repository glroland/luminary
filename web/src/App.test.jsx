import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import * as client from "./api/client.js";

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(client, "fetchSyncStatus").mockResolvedValue({ status: "idle" });
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
    vi.spyOn(client, "fetchTags").mockResolvedValue([]);
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
      expect(screen.getByAltText("beach.jpg")).toBeInTheDocument();
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
    // Anchored: the Manage page's own copy also contains "done" ("cannot be undone").
    expect(screen.getByText(/^done$/i)).toBeInTheDocument();
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
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    fireEvent.click(screen.getByRole("button", { name: /^by individual$/i }));
    fireEvent.click(screen.getByText("Alice"));

    await waitFor(() => expect(screen.queryByAltText("party.jpg")).not.toBeInTheDocument());
    expect(screen.getByAltText("beach.jpg")).toBeInTheDocument();

    // "All Images" clears the filter again.
    fireEvent.click(screen.getByText("All Images"));
    await waitFor(() => expect(screen.getByAltText("party.jpg")).toBeInTheDocument());
  });

  it("narrows the grid to a tag's photos when selected in the sidebar", async () => {
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([
      { id: "1", filename: "beach.jpg", width: 100, height: 100, created_at: new Date().toISOString() },
      { id: "2", filename: "party.jpg", width: 100, height: 100, created_at: new Date().toISOString() },
    ]);
    vi.spyOn(client, "fetchTags").mockResolvedValue([
      { id: "t1", name: "Favorites", created_at: new Date().toISOString(), photo_count: 1 },
    ]);
    const fetchTagPhotoIds = vi.spyOn(client, "fetchTagPhotoIds").mockResolvedValue(["2"]);

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    fireEvent.click(screen.getByRole("button", { name: /^by tag$/i }));
    fireEvent.click(screen.getByText("Favorites"));

    await waitFor(() => expect(screen.queryByAltText("beach.jpg")).not.toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();
    expect(fetchTagPhotoIds).toHaveBeenCalledWith("t1");

    fireEvent.click(screen.getByText("All Images"));
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
  });

  it("shows skeleton tiles while the photo list is loading", async () => {
    let resolvePhotos;
    vi.spyOn(client, "fetchPhotos").mockReturnValue(new Promise((resolve) => (resolvePhotos = resolve)));

    render(<App />);
    expect(screen.getByLabelText(/loading photos/i).querySelectorAll(".photo-skeleton").length).toBeGreaterThan(0);

    await act(async () => resolvePhotos([]));
    expect(screen.queryByLabelText(/loading photos/i)).not.toBeInTheDocument();
    expect(screen.getByText(/no photos yet/i)).toBeInTheDocument();
  });

  it("groups the grid under month headers when sorted by date, and drops them for a filename sort", async () => {
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([
      { id: "1", filename: "a.jpg", width: 100, height: 100, created_at: "2019-03-10T12:00:00" },
      { id: "2", filename: "b.jpg", width: 100, height: 100, created_at: "2019-03-02T12:00:00" },
      { id: "3", filename: "c.jpg", width: 100, height: 100, created_at: "2018-07-04T12:00:00" },
    ]);

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("a.jpg")).toBeInTheDocument());

    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings).toHaveLength(2);
    expect(headings[0]).toHaveTextContent(/2019/);
    expect(headings[0]).toHaveTextContent(/2$/); // two photos that month
    expect(headings[1]).toHaveTextContent(/2018/);
    expect(screen.getByText("3 photos")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/sort by/i), { target: { value: "filename-asc" } });
    expect(screen.queryAllByRole("heading", { level: 2 })).toHaveLength(0);
    expect(screen.getByAltText("c.jpg")).toBeInTheDocument();
  });

  it("steps between photos in the open modal with the arrow keys", async () => {
    const createdAt = new Date().toISOString();
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([
      { id: "1", filename: "beach.jpg", width: 100, height: 100, created_at: createdAt },
      { id: "2", filename: "party.jpg", width: 100, height: 100, created_at: createdAt },
    ]);
    vi.spyOn(client, "fetchFaces").mockResolvedValue([]);
    vi.spyOn(client, "fetchPhotoTags").mockResolvedValue([]);
    vi.spyOn(client, "fetchTags").mockResolvedValue([]);

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

    fireEvent.click(screen.getByAltText("beach.jpg"));
    await waitFor(() => expect(screen.getByRole("heading", { name: "beach.jpg" })).toBeInTheDocument());

    fireEvent.keyDown(window, { key: "ArrowRight" });
    await waitFor(() => expect(screen.getByRole("heading", { name: "party.jpg" })).toBeInTheDocument());

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "party.jpg" })).not.toBeInTheDocument());
  });

  it("toggles between the dark and light themes", async () => {
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([]);

    render(<App />);
    await waitFor(() => expect(screen.getByText(/no photos yet/i)).toBeInTheDocument());
    expect(document.documentElement.dataset.theme).toBe("dark");

    fireEvent.click(screen.getByRole("button", { name: /switch to light theme/i }));
    expect(document.documentElement.dataset.theme).toBe("light");

    fireEvent.click(screen.getByRole("button", { name: /switch to dark theme/i }));
    expect(document.documentElement.dataset.theme).toBe("dark");
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
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

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
    await waitFor(() => expect(screen.queryByAltText("beach.jpg")).not.toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /clear search/i }));
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();
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
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    fireEvent.change(screen.getByPlaceholderText(/search photo descriptions/i), {
      target: { value: "a party" },
    });
    const submitButton = document.querySelector(".sidebar-search-actions button[type='submit']");
    await act(async () => {
      fireEvent.click(submitButton);
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.queryByAltText("beach.jpg")).not.toBeInTheDocument());

    fireEvent.click(screen.getByText("All Images"));
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();
    // The clear ("x") button only shows while a search is active.
    expect(screen.queryByRole("button", { name: /clear search/i })).not.toBeInTheDocument();
  });
});
