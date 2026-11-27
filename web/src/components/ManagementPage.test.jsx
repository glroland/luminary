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
    vi.spyOn(client, "fetchHomes").mockResolvedValue([]);
    vi.spyOn(client, "fetchDismissedTrips").mockResolvedValue([]);
    // Expanding an individual also fetches its unmatched auto-tags.
    vi.spyOn(client, "fetchIndividualRejections").mockResolvedValue([]);
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

  it("adds a pet via the kind selector and badges it", async () => {
    const REX = { ...ALICE, id: "p9", name: "Rex", kind: "pet" };
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
    const createMock = vi.spyOn(client, "createIndividual").mockResolvedValue(REX);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText(/no individuals catalogued yet/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "pet" } });
    fireEvent.change(screen.getByPlaceholderText("Add a pet…"), { target: { value: "Rex" } });
    const form = screen.getByPlaceholderText("Add a pet…").closest("form");
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));

    expect(createMock).toHaveBeenCalledWith("Rex", "pet");
    await waitFor(() => expect(screen.getByText("Rex")).toBeInTheDocument());
    expect(screen.getByText("pet")).toBeInTheDocument();
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

  it("shows the source-photo popup after the face has been hovered for 3 seconds", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    vi.spyOn(client, "fetchIndividualFaces").mockResolvedValue([{ id: "f1", photo_id: "photo-1" }]);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    await waitFor(() => expect(document.querySelector(".management-face-image")).toBeInTheDocument());

    vi.useFakeTimers();
    try {
      fireEvent.mouseEnter(document.querySelector(".management-face-image"));
      await act(() => vi.advanceTimersByTimeAsync(2900));
      expect(screen.queryByAltText("Source photo for this face")).not.toBeInTheDocument();
      await act(() => vi.advanceTimersByTimeAsync(200));
      expect(screen.getByAltText("Source photo for this face")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
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

  it("lists unmatched auto-tags in a collapsed section and lets matching be allowed again", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    vi.spyOn(client, "fetchIndividualFaces").mockResolvedValue([]);
    vi.spyOn(client, "fetchIndividualRejections").mockResolvedValue([{ id: "r1", photo_id: "photo-9" }]);
    const deleteMock = vi.spyOn(client, "deleteRejection").mockResolvedValue();

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));

    const section = await waitFor(() => {
      const el = document.querySelector("details.management-rejections");
      expect(el).toBeInTheDocument();
      return el;
    });
    expect(section).not.toHaveAttribute("open");
    expect(screen.getByText("Unmatched auto-tags (1)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /allow matching again/i }));

    expect(deleteMock).toHaveBeenCalledWith("r1");
    await waitFor(() => expect(document.querySelector("details.management-rejections")).not.toBeInTheDocument());
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

  describe("background jobs (one at a time, shared by every browser)", () => {
    const idle = (extra = {}) => ({ status: "idle", processed: 0, total: 0, error: null, ...extra });
    const snapshot = (overrides = {}) => ({
      active: null,
      cancelling: false,
      sync: idle({ inserted: 0, updated: 0, deleted: 0, failed: 0 }),
      faces: idle({ matched: 0, full: false }),
      descriptions: idle({ updated: 0 }),
      locations: idle({ updated: 0 }),
      pets: idle({ found: 0 }),
      trips: idle({ created: 0, updated: 0, removed: 0, homes: 0 }),
      ...overrides,
    });
    const running = (extra = {}) => ({ status: "running", processed: 4, total: 10, error: null, ...extra });
    const renderPage = (jobs, props = {}) =>
      render(<ManagementPage jobs={jobs} onBack={() => {}} onNavigateToPhoto={() => {}} {...props} />);

    beforeEach(() => {
      vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    });

    it("shows every start button enabled when nothing is running", async () => {
      renderPage(snapshot());
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
      for (const name of ["Refresh", "Refresh Descriptions", "Refresh Locations", "Full Face Refresh"]) {
        expect(screen.getByRole("button", { name })).toBeEnabled();
      }
      expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    });

    it("shows a job another browser started: progress, a Cancel button, and every other start disabled", async () => {
      renderPage(snapshot({ active: "descriptions", descriptions: running({ updated: 3 }) }));
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

      expect(screen.getByText(/refreshing descriptions… 40%/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "Refresh Descriptions" })).not.toBeInTheDocument();
      for (const name of ["Refresh", "Refresh Locations", "Full Face Refresh"]) {
        expect(screen.getByRole("button", { name })).toBeDisabled();
      }
    });

    it("cancels the running job, and tells the parent to re-read the jobs", async () => {
      const after = snapshot({ active: "descriptions", cancelling: true, descriptions: running() });
      const cancelMock = vi.spyOn(client, "cancelJob").mockResolvedValue(after);
      const onJobsChange = vi.fn();

      renderPage(snapshot({ active: "descriptions", descriptions: running() }), { onJobsChange });
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      });
      expect(cancelMock).toHaveBeenCalled();
      expect(onJobsChange).toHaveBeenCalledWith(after);
    });

    it("starts the pets backfill from its own button and reports how many pets were found", async () => {
      const startMock = vi.spyOn(client, "refreshPets").mockResolvedValue({});
      const onJobsChange = vi.fn();
      const { rerender } = renderPage(snapshot(), { onJobsChange });
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "Scan for Pets" }));
      expect(startMock).toHaveBeenCalled();

      rerender(
        <ManagementPage
          jobs={snapshot({ active: "pets", pets: running({ found: 2 }) })}
          justFinished={null}
          onBack={() => {}}
          onNavigateToPhoto={() => {}}
        />
      );
      expect(screen.getByText("Looking for pets… 40%")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Scan for Pets" })).not.toBeInTheDocument();
    });

    it("shows 'Cancelling' (disabled) until the system is ready, then flips back to the start button", async () => {
      const { rerender } = renderPage(
        snapshot({ active: "locations", cancelling: true, locations: running() })
      );
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

      expect(screen.getByRole("button", { name: "Cancelling" })).toBeDisabled();
      expect(screen.queryByRole("button", { name: "Refresh Locations" })).not.toBeInTheDocument();

      rerender(
        <ManagementPage
          jobs={snapshot({ locations: { ...running(), status: "cancelled" } })}
          onBack={() => {}}
          onNavigateToPhoto={() => {}}
        />
      );
      expect(screen.getByRole("button", { name: "Refresh Locations" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: /cancelling/i })).not.toBeInTheDocument();
    });

    it("shows the outcome of a job that just ended for a moment, then clears it", async () => {
      const { rerender } = renderPage(snapshot({ active: "descriptions", descriptions: running() }));
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

      vi.useFakeTimers({ shouldAdvanceTime: true });
      await act(async () => {
        rerender(
          <ManagementPage
            jobs={snapshot({
              descriptions: { status: "completed", processed: 10, total: 10, updated: 8, error: null },
            })}
            onBack={() => {}}
            onNavigateToPhoto={() => {}}
          />
        );
      });
      expect(screen.getByText(/done -- 8 updated/i)).toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(2500));
      expect(screen.queryByText(/done -- 8 updated/i)).not.toBeInTheDocument();
    });

    it("starts a trips rebuild, shows its progress, and says what it did", async () => {
      const startMock = vi.spyOn(client, "refreshTrips").mockResolvedValueOnce({});
      const onJobsChange = vi.fn();
      const { rerender } = renderPage(snapshot(), { onJobsChange });
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "Rebuild Trips" }));
      expect(startMock).toHaveBeenCalledTimes(1);

      rerender(
        <ManagementPage
          jobs={snapshot({ active: "trips", trips: running({ created: 0, updated: 0, removed: 0, homes: 1 }) })}
          onBack={() => {}}
          onNavigateToPhoto={() => {}}
        />,
      );
      expect(screen.getByText(/finding trips… 40%/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    });

    it("says when no home was found, so no trips could be detected", async () => {
      const { rerender } = renderPage(snapshot({ active: "trips", trips: running() }));
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
      rerender(
        <ManagementPage
          jobs={snapshot({ trips: { status: "completed", processed: 5, total: 5, created: 0, updated: 0, removed: 0, homes: 0, error: null } })}
          onBack={() => {}}
          onNavigateToPhoto={() => {}}
        />,
      );
      expect(await screen.findByText(/no home location found/i)).toBeInTheDocument();
    });

    it("keeps a job's error up", async () => {
      renderPage(snapshot({ locations: { status: "error", processed: 0, total: 0, updated: 0, error: "Nominatim unreachable" } }));
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
      expect(screen.getByText(/refresh failed: nominatim unreachable/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Refresh Locations" })).toBeEnabled();
    });

    it("shows the face matching a sync triggers next to Refresh, cancellable, and reloads the catalog when it ends", async () => {
      const { rerender } = renderPage(
        snapshot({ active: "faces", faces: running({ matched: 1, full: false }) })
      );
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

      expect(screen.getByText(/matching faces… 40%/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Full Face Refresh" })).toBeDisabled();

      await act(async () => {
        rerender(
          <ManagementPage
            jobs={snapshot({
              faces: { status: "completed", processed: 10, total: 10, matched: 2, full: false, error: null },
            })}
            onBack={() => {}}
            onNavigateToPhoto={() => {}}
          />
        );
      });
      expect(screen.getByText(/faces done -- 2 matched/i)).toBeInTheDocument();
      expect(client.fetchIndividuals).toHaveBeenCalledTimes(2);
    });

    it("shows a running Full Face Refresh in the danger zone with its own Cancel", async () => {
      renderPage(snapshot({ active: "faces", faces: running({ matched: 1, full: true }) }));
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

      expect(screen.getByText(/redetecting & matching faces… 40%/i)).toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(1);
    });

    it("starts a job and asks the parent to re-read the jobs; a refusal (another job running) is shown", async () => {
      const startMock = vi.spyOn(client, "refreshLocations").mockResolvedValueOnce({});
      const onJobsChange = vi.fn();
      renderPage(snapshot(), { onJobsChange });
      await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Refresh Locations" }));
      });
      expect(startMock).toHaveBeenCalled();
      expect(onJobsChange).toHaveBeenCalled();

      startMock.mockRejectedValueOnce(new Error("Another job is already running (sync)."));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Refresh Locations" }));
      });
      expect(await screen.findByRole("alert")).toHaveTextContent(/another job is already running/i);
    });
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

  it("asks for confirmation before running 'Full Face Refresh', since it deletes all existing face tags", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const refreshMock = vi.spyOn(client, "refreshFaces").mockResolvedValue({
      status: "running",
      processed: 0,
      total: 0,
      matched: 0,
      full: true,
      error: null,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Full Face Refresh" }));
      await Promise.resolve();
    });

    expect(window.confirm).toHaveBeenCalled();
    expect(refreshMock).toHaveBeenCalledWith(true);
  });

  it("does not run 'Full Face Refresh' when the confirmation is declined", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const refreshMock = vi.spyOn(client, "refreshFaces").mockResolvedValue({
      status: "running",
      processed: 0,
      total: 0,
      matched: 0,
      full: true,
      error: null,
    });
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Full Face Refresh" }));

    expect(window.confirm).toHaveBeenCalled();
    expect(refreshMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Full Face Refresh" })).toBeInTheDocument();
  });

  it("downloads a duplicates CSV when 'Identify Duplicates' is clicked", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const downloadMock = vi.spyOn(client, "downloadDuplicatesCsv").mockResolvedValue();

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /identify duplicates/i }));

    expect(downloadMock).toHaveBeenCalledWith("all");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /identify duplicates/i })).not.toBeDisabled()
    );
  });

  it("passes the chosen duplicate type to the download", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    const downloadMock = vi.spyOn(client, "downloadDuplicatesCsv").mockResolvedValue();

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Duplicate type"), { target: { value: "exact" } });
    fireEvent.click(screen.getByRole("button", { name: /identify duplicates/i }));

    expect(downloadMock).toHaveBeenCalledWith("exact");
  });

  it("shows an error if the duplicates CSV download fails", async () => {
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([ALICE]);
    vi.spyOn(client, "downloadDuplicatesCsv").mockRejectedValue(new Error("Could not connect to the API."));

    render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /identify duplicates/i }));

    await waitFor(() => expect(screen.getByText("Could not connect to the API.")).toBeInTheDocument());
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

    it("downloads a tag's photo list CSV via the List button, placed before Rename", async () => {
      vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
      client.fetchTags.mockResolvedValue([VACATION]);
      const downloadMock = vi.spyOn(client, "downloadTagCsv").mockResolvedValue();

      render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());

      const row = screen.getByText("Vacation").closest("li");
      const labels = within(row).getAllByRole("button").map((button) => button.textContent);
      expect(labels.indexOf("List")).toBe(labels.indexOf("Rename") - 1);

      fireEvent.click(within(row).getByRole("button", { name: "List" }));
      expect(downloadMock).toHaveBeenCalledWith(VACATION);
    });

    it("shows an error if a tag's CSV download fails", async () => {
      vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
      client.fetchTags.mockResolvedValue([VACATION]);
      vi.spyOn(client, "downloadTagCsv").mockRejectedValue(new Error("Tag not found"));

      render(<ManagementPage onBack={() => {}} onNavigateToPhoto={() => {}} />);
      await waitFor(() => expect(screen.getByText("Vacation")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "List" }));
      await waitFor(() => expect(screen.getByText("Tag not found")).toBeInTheDocument());
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
