import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ManagementPage from "./ManagementPage.jsx";
import * as client from "../api/client.js";

const ALICE = { id: "p1", name: "Alice", has_representative_thumbnail: false, created_at: "2024-01-01T00:00:00Z" };
const BOB = { id: "p2", name: "Bob", has_representative_thumbnail: false, created_at: "2024-01-01T00:00:00Z" };

describe("ManagementPage", () => {
  beforeEach(() => {
    // Every render triggers a tags fetch alongside the individuals one --
    // defaulted to empty here so tests that don't care about tags aren't
    // affected by it; tests that do override this themselves.
    vi.spyOn(client, "fetchTags").mockResolvedValue([]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("lists catalogued individuals", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE, BOB]);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
      expect(screen.getByText("Bob")).toBeInTheDocument();
    });
  });

  it("shows an empty state when there are none", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);

    await waitFor(() => expect(screen.getByText(/no individuals catalogued yet/i)).toBeInTheDocument());
  });

  it("calls onBack when the back link is clicked", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
    const onBack = vi.fn();

    render(<ManagementPage onBack={onBack} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText(/no individuals catalogued yet/i)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/back to gallery/i));
    expect(onBack).toHaveBeenCalled();
  });

  it("adds a new individual via the form", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
    const createMock = vi.spyOn(client, "createIndividual").mockResolvedValue(ALICE);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText(/no individuals catalogued yet/i)).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Add a person…"), { target: { value: "Alice" } });
    const form = screen.getByPlaceholderText("Add a person…").closest("form");
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));

    expect(createMock).toHaveBeenCalledWith("Alice");
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
  });

  it("renames an individual", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const updateMock = vi
      .spyOn(client, "updateIndividual")
      .mockResolvedValue({ ...ALICE, name: "Alicia" });

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByDisplayValue("Alice");
    fireEvent.change(input, { target: { value: "Alicia" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(updateMock).toHaveBeenCalledWith("p1", "Alicia");
    await waitFor(() => expect(screen.getByText("Alicia")).toBeInTheDocument());
  });

  it("deletes an individual", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const deleteMock = vi.spyOn(client, "deleteIndividual").mockResolvedValue();

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(deleteMock).toHaveBeenCalledWith("p1");
    await waitFor(() => expect(screen.queryByText("Alice")).not.toBeInTheDocument());
  });

  it("expands an individual's accordion and lists their tagged faces, lazily fetched", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const facesMock = vi
      .spyOn(client, "fetchIndividualFaces")
      .mockResolvedValue([{ id: "f1", photo_id: "photo-1" }]);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    expect(facesMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));

    expect(facesMock).toHaveBeenCalledWith("p1");
    await waitFor(() => expect(document.querySelector(".management-face")).toBeInTheDocument());
  });

  it("navigates to the source photo when a tagged face is clicked", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    vi.spyOn(client, "fetchIndividualFaces").mockResolvedValue([{ id: "f1", photo_id: "photo-1" }]);
    const onNavigateToPhoto = vi.fn();

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={onNavigateToPhoto} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    await waitFor(() => expect(document.querySelector(".management-face-image")).toBeInTheDocument());

    fireEvent.click(document.querySelector(".management-face-image"));

    expect(onNavigateToPhoto).toHaveBeenCalledWith("photo-1");
  });

  it("removes a tag (untags the face) from the accordion", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    vi.spyOn(client, "fetchIndividualFaces").mockResolvedValue([{ id: "f1", photo_id: "photo-1" }]);
    const untagMock = vi.spyOn(client, "untagFace").mockResolvedValue();

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    await waitFor(() => expect(document.querySelector(".management-face")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /remove this tag/i }));

    expect(untagMock).toHaveBeenCalledWith("f1");
    await waitFor(() => expect(document.querySelector(".management-face")).not.toBeInTheDocument());
  });

  it("shows an 'auto' badge on auto-matched faces but not manually tagged ones", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    vi.spyOn(client, "fetchIndividualFaces").mockResolvedValue([
      { id: "f1", photo_id: "photo-1", tag_source: "manual" },
      { id: "f2", photo_id: "photo-2", tag_source: "auto" },
    ]);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));

    await waitFor(() => expect(document.querySelectorAll(".management-face")).toHaveLength(2));
    expect(document.querySelectorAll(".management-face-auto-badge")).toHaveLength(1);
  });

  it("runs 'Refresh Faces' and reflects progress through to completion, then reloads the catalog", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const refreshMock = vi
      .spyOn(client, "refreshFaces")
      .mockResolvedValue({ status: "running", processed: 0, total: 10, matched: 0, error: null });

    const statusMock = vi.spyOn(client, "fetchFaceRefreshStatus");
    // First call is the mount-time "is a refresh already running?" check
    // (see ManagementPage.jsx's mount effect) -- idle, so it doesn't
    // preempt the click below. The next two are the poll loop started by
    // that click.
    statusMock.mockResolvedValueOnce({ status: "idle", processed: 0, total: 0, matched: 0, error: null });
    statusMock.mockResolvedValueOnce({ status: "running", processed: 4, total: 10, matched: 1, error: null });
    statusMock.mockResolvedValue({ status: "completed", processed: 10, total: 10, matched: 3, error: null });

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    vi.useFakeTimers({ shouldAdvanceTime: true });

    // Optimistic progress UI appears immediately, before the first poll.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /refresh faces/i }));
      await Promise.resolve();
    });
    expect(refreshMock).toHaveBeenCalled();
    expect(screen.getByRole("status")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/matching faces… 40%/i)).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/done -- 3 matched/i)).toBeInTheDocument();
    // Initial mount's fetch, plus one more once the job completed.
    expect(client.fetchIndividuals).toHaveBeenCalledTimes(2);

    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByRole("button", { name: /refresh faces/i })).toBeInTheDocument();
  });

  it("picks up and polls 'Refresh Faces' progress automatically once a sync completes, without a click", async () => {
    // The backend auto-triggers "Refresh Faces" as soon as a sync finishes
    // (sync.py's _trigger_face_refresh) -- this simulates App.jsx handing
    // down a "completed" syncStatus and expects ManagementPage to notice
    // and start polling on its own, exactly as if the standalone button
    // had been clicked.
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);

    const statusMock = vi.spyOn(client, "fetchFaceRefreshStatus");
    statusMock.mockResolvedValueOnce({ status: "idle", processed: 0, total: 0, matched: 0, error: null }); // mount check
    statusMock.mockResolvedValueOnce({ status: "running", processed: 4, total: 10, matched: 1, error: null });
    statusMock.mockResolvedValue({ status: "completed", processed: 10, total: 10, matched: 2, error: null });

    const { rerender } = render(
      <ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} syncStatus={null} onRefresh={() => {}} />
    );
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /refresh faces/i })).toBeInTheDocument();

    vi.useFakeTimers({ shouldAdvanceTime: true });

    await act(async () => {
      rerender(
        <ManagementPage
          onBack={() => {}}
          onNavigateToPhoto={() => {}}
          syncStatus={{ status: "completed", processed: 10, total: 10, inserted: 10, updated: 0, deleted: 0 }}
          onRefresh={() => {}}
        />
      );
      await Promise.resolve();
    });

    await waitFor(() => expect(screen.getByText(/matching faces… 40%/i)).toBeInTheDocument());

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/done -- 2 matched/i)).toBeInTheDocument();
    // Initial mount's fetch, plus one more once the auto-triggered job completed.
    expect(client.fetchIndividuals).toHaveBeenCalledTimes(2);
  });

  it("renders the library Refresh control (moved here from the sidebar) and calls onRefresh", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
    const onRefresh = vi.fn();

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} onRefresh={onRefresh} syncStatus={null} />);
    await waitFor(() => expect(screen.getByText(/no individuals catalogued yet/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^refresh$/i }));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("purges the database after confirming, clears the catalog, and notifies the parent", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const purgeMock = vi.spyOn(client, "purgeDatabase").mockResolvedValue();
    const onPurged = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} onPurged={onPurged} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /purge database/i }));

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(purgeMock).toHaveBeenCalled());
    expect(onPurged).toHaveBeenCalled();
    expect(screen.queryByText("Alice")).not.toBeInTheDocument();
  });

  it("does not purge when the confirmation is declined", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const purgeMock = vi.spyOn(client, "purgeDatabase").mockResolvedValue();
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} onPurged={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /purge database/i }));

    expect(window.confirm).toHaveBeenCalled();
    expect(purgeMock).not.toHaveBeenCalled();
    expect(screen.getByText("Alice")).toBeInTheDocument();
  });

  it("shows a failure message if 'Refresh Faces' errors", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    vi.spyOn(client, "refreshFaces").mockResolvedValue({
      status: "running",
      processed: 0,
      total: 0,
      matched: 0,
      error: null,
    });
    vi.spyOn(client, "fetchFaceRefreshStatus").mockResolvedValue({
      status: "error",
      processed: 0,
      total: 0,
      matched: 0,
      error: "face recognition requires torch/facenet-pytorch to be installed",
    });

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    vi.useFakeTimers({ shouldAdvanceTime: true });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /refresh faces/i }));
      await Promise.resolve();
    });

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/refresh failed/i)).toBeInTheDocument();
  });

  it("runs 'Refresh Descriptions' and reflects progress through to completion", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const refreshMock = vi
      .spyOn(client, "refreshDescriptions")
      .mockResolvedValue({ status: "running", processed: 0, total: 10, updated: 0, error: null });

    const statusMock = vi.spyOn(client, "fetchDescriptionRefreshStatus");
    // First call is the mount-time "is a refresh already running?" check --
    // idle, so it doesn't preempt the click below. The next two are the
    // poll loop started by that click.
    statusMock.mockResolvedValueOnce({ status: "idle", processed: 0, total: 0, updated: 0, error: null });
    statusMock.mockResolvedValueOnce({ status: "running", processed: 4, total: 10, updated: 3, error: null });
    statusMock.mockResolvedValue({ status: "completed", processed: 10, total: 10, updated: 8, error: null });

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    vi.useFakeTimers({ shouldAdvanceTime: true });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /refresh descriptions/i }));
      await Promise.resolve();
    });
    expect(refreshMock).toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/refreshing descriptions… 40%/i)).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/done -- 8 updated/i)).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByRole("button", { name: /refresh descriptions/i })).toBeInTheDocument();
  });

  it("shows a failure message if 'Refresh Descriptions' errors", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    vi.spyOn(client, "refreshDescriptions").mockResolvedValue({
      status: "running",
      processed: 0,
      total: 0,
      updated: 0,
      error: null,
    });
    vi.spyOn(client, "fetchDescriptionRefreshStatus").mockResolvedValue({
      status: "error",
      processed: 0,
      total: 0,
      updated: 0,
      error: "description enrichment isn't configured",
    });

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    vi.useFakeTimers({ shouldAdvanceTime: true });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /refresh descriptions/i }));
      await Promise.resolve();
    });

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/refresh failed/i)).toBeInTheDocument();
  });

  it("runs 'Refresh Locations' and reflects progress through to completion", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const refreshMock = vi
      .spyOn(client, "refreshLocations")
      .mockResolvedValue({ status: "running", processed: 0, total: 10, updated: 0, error: null });

    const statusMock = vi.spyOn(client, "fetchLocationRefreshStatus");
    // First call is the mount-time "is a refresh already running?" check --
    // idle, so it doesn't preempt the click below. The next two are the
    // poll loop started by that click.
    statusMock.mockResolvedValueOnce({ status: "idle", processed: 0, total: 0, updated: 0, error: null });
    statusMock.mockResolvedValueOnce({ status: "running", processed: 4, total: 10, updated: 3, error: null });
    statusMock.mockResolvedValue({ status: "completed", processed: 10, total: 10, updated: 8, error: null });

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    vi.useFakeTimers({ shouldAdvanceTime: true });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /refresh locations/i }));
      await Promise.resolve();
    });
    expect(refreshMock).toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/refreshing locations… 40%/i)).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/done -- 8 updated/i)).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByRole("button", { name: /refresh locations/i })).toBeInTheDocument();
  });

  it("shows a failure message if 'Refresh Locations' errors", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    vi.spyOn(client, "refreshLocations").mockResolvedValue({
      status: "running",
      processed: 0,
      total: 0,
      updated: 0,
      error: null,
    });
    vi.spyOn(client, "fetchLocationRefreshStatus").mockResolvedValue({
      status: "error",
      processed: 0,
      total: 0,
      updated: 0,
      error: "Nominatim unreachable",
    });

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    vi.useFakeTimers({ shouldAdvanceTime: true });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /refresh locations/i }));
      await Promise.resolve();
    });

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(/refresh failed/i)).toBeInTheDocument();
  });

  describe("tags", () => {
    const VACATION = { id: "t1", name: "Vacation", created_at: "2024-01-01T00:00:00Z", photo_count: 3 };
    const PETS = { id: "t2", name: "Pets", created_at: "2024-01-01T00:00:00Z", photo_count: 1 };

    it("lists catalogued tags with their photo counts, but no photos themselves", async () => {
      vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
      client.fetchTags.mockResolvedValue([VACATION, PETS]);

      render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);

      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());
      expect(screen.getByText("3 photos")).toBeInTheDocument();
      expect(screen.getByText("Pets")).toBeInTheDocument();
      expect(screen.getByText("1 photo")).toBeInTheDocument();
      expect(document.querySelector(".management-tags img")).not.toBeInTheDocument();
    });

    it("shows an empty state when there are no tags", async () => {
      vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
      client.fetchTags.mockResolvedValue([]);

      render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);

      await waitFor(() => expect(screen.getByText(/no tags catalogued yet/i)).toBeInTheDocument());
    });

    it("adds a new tag via the form", async () => {
      vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
      client.fetchTags.mockResolvedValue([]);
      const createMock = vi.spyOn(client, "createTag").mockResolvedValue(VACATION);

      render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
      await waitFor(() => expect(screen.getByText(/no tags catalogued yet/i)).toBeInTheDocument());

      fireEvent.change(screen.getByPlaceholderText("Add a tag…"), { target: { value: "Vacation" } });
      const form = screen.getByPlaceholderText("Add a tag…").closest("form");
      fireEvent.click(within(form).getByRole("button", { name: "Add" }));

      expect(createMock).toHaveBeenCalledWith("Vacation");
      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());
    });

    it("renames a tag", async () => {
      vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
      client.fetchTags.mockResolvedValue([VACATION]);
      const updateMock = vi.spyOn(client, "updateTag").mockResolvedValue({ ...VACATION, name: "Travel" });

      render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "Rename" }));
      const input = screen.getByDisplayValue("Vacation");
      fireEvent.change(input, { target: { value: "Travel" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      expect(updateMock).toHaveBeenCalledWith("t1", "Travel");
      await waitFor(() => expect(screen.getByText("Travel")).toBeInTheDocument());
    });

    it("deletes a tag", async () => {
      vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
      client.fetchTags.mockResolvedValue([VACATION]);
      const deleteMock = vi.spyOn(client, "deleteTag").mockResolvedValue();

      render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "Delete" }));

      expect(deleteMock).toHaveBeenCalledWith("t1");
      await waitFor(() => expect(screen.queryByText("Vacation")).not.toBeInTheDocument());
    });
  });
});
