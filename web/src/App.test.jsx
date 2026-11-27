import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import L from "leaflet";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import * as client from "./api/client.js";

const idleState = { status: "idle", processed: 0, total: 0, error: null };
const IDLE_JOBS = {
  active: null,
  cancelling: false,
  sync: { ...idleState, inserted: 0, updated: 0, deleted: 0, failed: 0 },
  faces: { ...idleState, matched: 0, full: false },
  descriptions: { ...idleState, updated: 0 },
  locations: { ...idleState, updated: 0 },
  pets: { ...idleState, found: 0 },
  trips: { ...idleState, created: 0, updated: 0, removed: 0, homes: 0 },
  duplicates: { ...idleState, found: 0, mode: null },
};

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(client, "fetchJobs").mockResolvedValue(IDLE_JOBS);
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
    vi.spyOn(client, "fetchIndividualGroups").mockResolvedValue([]);
    vi.spyOn(client, "fetchTags").mockResolvedValue([]);
    vi.spyOn(client, "fetchTrips").mockResolvedValue([]);
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

  it("starts a sync from the Refresh button and reflects the shared job status through to completion", async () => {
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([]);
    vi.spyOn(client, "startSync").mockResolvedValue({ status: "running" });

    const syncDone = { status: "completed", processed: 10, total: 10, inserted: 10, updated: 0, deleted: 0, failed: 0, error: null };
    const jobsMock = vi.spyOn(client, "fetchJobs");
    jobsMock.mockResolvedValueOnce(IDLE_JOBS); // initial mount check
    jobsMock.mockResolvedValueOnce(IDLE_JOBS); // Manage page opened
    jobsMock.mockResolvedValueOnce({ ...IDLE_JOBS, active: "sync", sync: { ...syncDone, status: "running", processed: 4 } });
    jobsMock.mockResolvedValue({ ...IDLE_JOBS, sync: syncDone });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/no photos yet/i)).toBeInTheDocument();
    });

    vi.useFakeTimers({ shouldAdvanceTime: true });

    // Refresh lives on the Manage page -- navigate there like a user would.
    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    fireEvent.click(screen.getByText("Manage"));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^refresh$/i }));
      await Promise.resolve();
    });
    expect(client.startSync).toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/syncing 40%/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    // Anchored: the Manage page's own copy also contains "done" ("cannot be undone").
    expect(screen.getByText(/^done$/i)).toBeInTheDocument();
    expect(client.fetchPhotos).toHaveBeenCalledTimes(2);

    await act(() => vi.advanceTimersByTimeAsync(2500));
    expect(screen.getByRole("button", { name: /^refresh$/i })).toBeEnabled();
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

  // Search runs through the chat: the reply carries the matching photo
  // ids, which become the gallery's "Search Results".
  async function searchViaChat(message) {
    // The popup stays open once opened (a second search in one test).
    const launcher = screen.queryByRole("button", { name: /open chat/i });
    if (launcher) fireEvent.click(launcher);
    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: message } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /send/i }));
      await Promise.resolve();
    });
  }

  function mockTwoPhotos() {
    const createdAt = new Date().toISOString();
    vi.spyOn(client, "fetchPhotos").mockResolvedValue([
      { id: "1", filename: "beach.jpg", width: 100, height: 100, created_at: createdAt },
      { id: "2", filename: "party.jpg", width: 100, height: 100, created_at: createdAt },
    ]);
  }

  it("shows a chat search's matches as Search Results, and All Images clears it", async () => {
    mockTwoPhotos();
    const party = { id: "2", filename: "party.jpg", score: 0.874 };
    const chatMock = vi
      .spyOn(client, "chatAboutPhoto")
      .mockResolvedValue({ reply: "Found one party photo.", boxes: [], searchResults: [party] });

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

    await searchViaChat("find party photos");

    // No photo is open, so the chat is about the library as a whole.
    // There is no search list yet to send along.
    expect(chatMock).toHaveBeenCalledWith(null, "find party photos", [], expect.any(Function), null, expect.any(Function));
    await waitFor(() => expect(screen.queryByAltText("beach.jpg")).not.toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();
    // Each result's match score is on its tile.
    expect(screen.getByText("87%")).toHaveClass("photo-card-score");
    expect(screen.getByText("Found one party photo.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search Results" })).toHaveClass("sidebar-all-images-active");

    fireEvent.click(screen.getByText("All Images"));
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();

    // Outside the search results a tile carries no score.
    expect(screen.queryByText("87%")).not.toBeInTheDocument();

    // The row brings the last search back.
    fireEvent.click(screen.getByRole("button", { name: "Search Results" }));
    await waitFor(() => expect(screen.queryByAltText("beach.jpg")).not.toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();

    // The next message carries the search list back, for the model's tools
    // to filter or truncate -- it isn't part of the conversation text.
    await searchViaChat("only the best ones");
    expect(chatMock).toHaveBeenLastCalledWith(null, "only the best ones", expect.any(Array), expect.any(Function), [party], expect.any(Function));
  });

  it("reloads the photo list when a search returns photos it doesn't have yet", async () => {
    const createdAt = new Date().toISOString();
    const stale = [{ id: "1", filename: "beach.jpg", width: 100, height: 100, created_at: createdAt }];
    const fresh = [
      { id: "9", filename: "graduation.jpg", width: 100, height: 100, created_at: createdAt },
      ...stale,
    ];
    const fetchMock = vi.spyOn(client, "fetchPhotos").mockResolvedValueOnce(stale).mockResolvedValue(fresh);
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({
      reply: "Found it.",
      boxes: [],
      searchResults: [{ id: "9", filename: "graduation.jpg", score: 0.9 }],
    });

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

    await searchViaChat("find graduation photos");

    await waitFor(() => expect(screen.getByAltText("graduation.jpg")).toBeInTheDocument());
    expect(screen.queryByText(/no photos match/i)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows a Reset button only in search results, and it returns to all photos", async () => {
    mockTwoPhotos();
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({
      reply: "Found one.",
      boxes: [],
      searchResults: [{ id: "2", filename: "party.jpg", score: 0.9 }],
    });

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();

    await searchViaChat("find party");
    await waitFor(() => expect(screen.getByText("90%")).toBeInTheDocument());
    expect(screen.queryByAltText("beach.jpg")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reset" }));

    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
  });

  it("clears the search from the sidebar's X button", async () => {
    mockTwoPhotos();
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({
      reply: "Found one.",
      boxes: [],
      searchResults: [{ id: "2", filename: "party.jpg", score: 0.9 }],
    });

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /toggle sidebar/i }));
    expect(screen.queryByRole("button", { name: /clear search results/i })).not.toBeInTheDocument();

    await searchViaChat("find party");
    await waitFor(() => expect(screen.getByText("90%")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /clear search results/i }));

    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /clear search results/i })).not.toBeInTheDocument();
  });

  it("shows search results in the order given: best score first", async () => {
    mockTwoPhotos();
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({
      reply: "Found two.",
      boxes: [],
      searchResults: [
        { id: "2", filename: "party.jpg", score: 0.9 },
        { id: "gone", filename: "gone.jpg", score: 0.8 },
        { id: "1", filename: "beach.jpg", score: 0.4 },
      ],
    });

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

    await searchViaChat("find photos");

    await waitFor(() => expect(screen.getByText("90%")).toBeInTheDocument());
    // An id that isn't in the loaded list is skipped.
    expect(screen.getAllByRole("img").map((img) => img.alt).filter(Boolean)).toEqual(["party.jpg", "beach.jpg"]);
    expect(screen.getByText("40%")).toBeInTheDocument();
  });

  it("closes the open photo to show a search's results, keeping the conversation", async () => {
    mockTwoPhotos();
    const chatMock = vi
      .spyOn(client, "chatAboutPhoto")
      .mockResolvedValue({
        reply: "Found the party.",
        boxes: [],
        searchResults: [{ id: "2", filename: "party.jpg", score: 1 }],
      });

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

    fireEvent.click(screen.getByAltText("beach.jpg"));
    await waitFor(() => expect(document.querySelector(".photo-modal")).toBeInTheDocument());

    await searchViaChat("find party photos");

    // The open photo is what the model was shown.
    expect(chatMock).toHaveBeenCalledWith("1", "find party photos", [], expect.any(Function), null, expect.any(Function));
    await waitFor(() => expect(document.querySelector(".photo-modal")).not.toBeInTheDocument());
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();
    expect(screen.queryByAltText("beach.jpg")).not.toBeInTheDocument();
    expect(screen.getByText("Found the party.")).toBeInTheDocument();
  });

  it("leaves the gallery alone when a chat reply didn't search", async () => {
    mockTwoPhotos();
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({ reply: "Hello!", boxes: [], searchResults: null });

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

    await searchViaChat("hi");

    await waitFor(() => expect(screen.getByText("Hello!")).toBeInTheDocument());
    expect(screen.getByAltText("beach.jpg")).toBeInTheDocument();
    expect(screen.getByAltText("party.jpg")).toBeInTheDocument();
  });

  it("says so when a search matched nothing, or none has been run yet", async () => {
    mockTwoPhotos();
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({ reply: "Nothing found.", boxes: [], searchResults: [] });

    render(<App />);
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Search Results" }));
    expect(screen.getByText(/no search yet/i)).toBeInTheDocument();

    await searchViaChat("find unicorns");
    await waitFor(() => expect(screen.getByText(/no photos match your search/i)).toBeInTheDocument());
  });

  describe("trips", () => {
    const TRIP = {
      id: "t1",
      name: "Boone, North Carolina, May 2019",
      kind: "trip",
      status: "active",
      start_at: "2019-05-10T09:00:00",
      end_at: "2019-05-11T17:00:00",
      photo_count: 2,
    };

    function mockTrip() {
      vi.spyOn(L, "polyline").mockReturnValue({ addTo: vi.fn() }); // jsdom has no SVG
      vi.spyOn(client, "fetchPhotos").mockResolvedValue(
        ["1", "2", "3"].map((id) => ({
          id,
          filename: `${id}.jpg`,
          width: 100,
          height: 100,
          created_at: id === "3" ? "2020-01-01T10:00:00+00:00" : `2019-05-1${id}T09:00:00+00:00`,
        })),
      );
      vi.spyOn(client, "fetchTrips").mockResolvedValue([TRIP]);
      vi.spyOn(client, "fetchTripPhotoIds").mockResolvedValue(["1", "2"]);
      vi.spyOn(client, "fetchTripLocations").mockResolvedValue({
        stops: [
          { latitude: 36.2, longitude: -81.6, count: 1, first_at: "2019-05-10T09:00:00", last_at: "2019-05-10T09:00:00", place: "Boone", cover_photo_id: "1", photo_ids: ["1"] },
          { latitude: 36.1, longitude: -81.7, count: 1, first_at: "2019-05-11T09:00:00", last_at: "2019-05-11T09:00:00", place: "Blowing Rock", cover_photo_id: "2", photo_ids: ["2"] },
        ],
        route: [0, 1],
        home: null,
        unlocated_count: 0,
      });
    }

    async function openTrip() {
      render(<App />);
      await waitFor(() => expect(screen.getByAltText("3.jpg")).toBeInTheDocument());
      fireEvent.click(screen.getByRole("button", { name: /^by trip$/i }));
      fireEvent.click(await screen.findByText("2019"));
      fireEvent.click(screen.getByText(TRIP.name));
    }

    it("narrows the gallery to a selected trip and shows its map", async () => {
      mockTrip();
      await openTrip();

      expect(await screen.findByRole("heading", { name: TRIP.name })).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByAltText("3.jpg")).not.toBeInTheDocument());
      expect(screen.getByAltText("1.jpg")).toBeInTheDocument();
      expect(screen.getByAltText("2.jpg")).toBeInTheDocument();
      expect(client.fetchTripPhotoIds).toHaveBeenCalledWith("t1");
      await waitFor(() => expect(document.querySelectorAll(".trip-stop-marker")).toHaveLength(2));
      // A trip is always chronological, so the sort control goes away.
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    });

    it("narrows to one map stop when it is clicked, and back again", async () => {
      mockTrip();
      await openTrip();
      await waitFor(() => expect(document.querySelectorAll(".trip-stop-marker")).toHaveLength(2));

      fireEvent.click(document.querySelector(".trip-stop-marker"));

      await waitFor(() => expect(screen.queryByAltText("2.jpg")).not.toBeInTheDocument());
      expect(screen.getByAltText("1.jpg")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /show the whole trip/i }));
      await waitFor(() => expect(screen.getByAltText("2.jpg")).toBeInTheDocument());
    });

    it("leaves the trip view when All Images is chosen", async () => {
      mockTrip();
      await openTrip();
      await screen.findByRole("heading", { name: TRIP.name });

      fireEvent.click(screen.getByRole("button", { name: /all images/i }));

      await waitFor(() => expect(screen.getByAltText("3.jpg")).toBeInTheDocument());
      expect(screen.queryByRole("heading", { name: TRIP.name })).not.toBeInTheDocument();
    });

    describe("editing", () => {
      it("renames the selected trip and reloads the list", async () => {
        mockTrip();
        const update = vi.spyOn(client, "updateTrip").mockResolvedValue({ ...TRIP, name: "Mountains" });
        await openTrip();
        await screen.findByRole("heading", { name: TRIP.name });
        client.fetchTrips.mockResolvedValue([{ ...TRIP, name: "Mountains" }]);

        fireEvent.click(screen.getByRole("button", { name: "Rename" }));
        fireEvent.change(screen.getByLabelText("Trip name"), { target: { value: "Mountains" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(update).toHaveBeenCalledWith("t1", { name: "Mountains" }));
        expect(await screen.findByRole("heading", { name: "Mountains" })).toBeInTheDocument();
      });

      it("merges into another trip and shows the survivor", async () => {
        mockTrip();
        const other = { ...TRIP, id: "t2", name: "Florida, Dec 2019", start_at: "2019-12-01T09:00:00" };
        client.fetchTrips.mockResolvedValue([TRIP, other]);
        const merge = vi.spyOn(client, "mergeTrips").mockResolvedValue(TRIP);
        await openTrip();
        await screen.findByRole("heading", { name: TRIP.name });

        fireEvent.click(screen.getByRole("button", { name: /merge with/i }));
        fireEvent.click(screen.getByRole("button", { name: "Merge" }));

        await waitFor(() => expect(merge).toHaveBeenCalledWith(["t1", "t2"]));
        expect(await screen.findByRole("heading", { name: TRIP.name })).toBeInTheDocument();
      });

      it("dismisses the trip and goes back to the whole gallery", async () => {
        mockTrip();
        const dismiss = vi.spyOn(client, "dismissTrip").mockResolvedValue();
        vi.spyOn(window, "confirm").mockReturnValue(true);
        await openTrip();
        await screen.findByRole("heading", { name: TRIP.name });
        client.fetchTrips.mockResolvedValue([]);

        fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

        await waitFor(() => expect(dismiss).toHaveBeenCalledWith("t1"));
        await waitFor(() => expect(screen.queryByRole("heading", { name: TRIP.name })).not.toBeInTheDocument());
        expect(screen.getByAltText("3.jpg")).toBeInTheDocument();
      });

      it("adds a photo to a trip from the gallery's right-click menu", async () => {
        mockTrip();
        vi.spyOn(client, "fetchPhotoTags").mockResolvedValue([]);
        const add = vi.spyOn(client, "addTripPhotos").mockResolvedValue(TRIP);
        render(<App />);
        await waitFor(() => expect(screen.getByAltText("3.jpg")).toBeInTheDocument());

        fireEvent.contextMenu(screen.getByAltText("3.jpg").closest(".photo-card"), { clientX: 5, clientY: 5 });
        fireEvent.click(await screen.findByText(/add to trip/i));
        fireEvent.click(screen.getByText(TRIP.name));

        await waitFor(() => expect(add).toHaveBeenCalledWith("t1", ["3"]));
      });
    });
  });
});
