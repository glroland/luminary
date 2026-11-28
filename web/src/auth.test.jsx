import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import { AuthProvider } from "./auth.jsx";
import * as client from "./api/client.js";

const idle = { status: "idle", processed: 0, total: 0, error: null };
const JOBS = {
  active: null,
  cancelling: false,
  sync: { ...idle, inserted: 0, updated: 0, deleted: 0, failed: 0 },
  faces: { ...idle, matched: 0, full: false },
  descriptions: { ...idle, updated: 0 },
  locations: { ...idle, updated: 0 },
  pets: { ...idle, found: 0 },
  trips: { ...idle, created: 0, updated: 0, removed: 0, homes: 0 },
  duplicates: { ...idle, found: 0, mode: null },
};

const PHOTO = {
  id: "1",
  filename: "beach.jpg",
  width: 100,
  height: 100,
  size_bytes: 1,
  content_type: "image/jpeg",
  created_at: "2024-05-01T10:00:00",
};

const GUEST = { auth_enabled: true, guest_mode: true, authenticated: true, role: "guest", display_name: "Guest" };
const ADMIN = { auth_enabled: true, guest_mode: true, authenticated: true, role: "admin", display_name: "Admin" };
const SIGNED_OUT = { auth_enabled: true, guest_mode: false, authenticated: false, role: null, display_name: null };

function renderApp() {
  return render(
    <AuthProvider>
      <App />
    </AuthProvider>
  );
}

describe("authentication in the GUI", () => {
  let fetchPhotos;
  let fetchJobs;

  beforeEach(() => {
    vi.restoreAllMocks();
    fetchPhotos = vi.spyOn(client, "fetchPhotos").mockResolvedValue([PHOTO]);
    fetchJobs = vi.spyOn(client, "fetchJobs").mockResolvedValue(JOBS);
    vi.spyOn(client, "fetchIndividuals").mockResolvedValue([]);
    vi.spyOn(client, "fetchIndividualGroups").mockResolvedValue([]);
    vi.spyOn(client, "fetchTags").mockResolvedValue([]);
    vi.spyOn(client, "fetchTrips").mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows a guest the gallery without Manage, and offers sign-in", async () => {
    vi.spyOn(client, "fetchSession").mockResolvedValue(GUEST);
    renderApp();

    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    expect(screen.queryByText("Manage")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
    // Job status is admin-only, so a guest never asks for it.
    expect(fetchJobs).not.toHaveBeenCalled();
  });

  it("gives a guest no tag menu on right-click", async () => {
    vi.spyOn(client, "fetchSession").mockResolvedValue(GUEST);
    renderApp();

    const tile = await screen.findByAltText("beach.jpg");
    fireEvent.contextMenu(tile);
    expect(document.querySelector(".tag-menu")).toBeNull();
  });

  it("shows an admin Manage and Sign out", async () => {
    vi.spyOn(client, "fetchSession").mockResolvedValue(ADMIN);
    renderApp();

    await waitFor(() => expect(screen.getByText("Manage")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in" })).not.toBeInTheDocument();
    await waitFor(() => expect(fetchJobs).toHaveBeenCalled());

    fireEvent.contextMenu(await screen.findByAltText("beach.jpg"));
    expect(document.querySelector(".tag-menu")).not.toBeNull();
  });

  it("signing in as admin from guest mode unlocks Manage", async () => {
    vi.spyOn(client, "fetchSession").mockResolvedValue(GUEST);
    const login = vi.spyOn(client, "login").mockResolvedValue(ADMIN);
    renderApp();

    await screen.findByAltText("beach.jpg");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Sign in" }).pop());

    await waitFor(() => expect(login).toHaveBeenCalledWith("secret"));
    await waitFor(() => expect(screen.getByText("Manage")).toBeInTheDocument());
  });

  it("shows only the login screen when sign-in is required, and loads nothing", async () => {
    vi.spyOn(client, "fetchSession").mockResolvedValue(SIGNED_OUT);
    renderApp();

    await waitFor(() => expect(screen.getByLabelText("Password")).toBeInTheDocument());
    expect(fetchPhotos).not.toHaveBeenCalled();
    expect(screen.queryByText("Manage")).not.toBeInTheDocument();
  });

  it("loads the gallery once a required sign-in succeeds, and shows a refusal", async () => {
    vi.spyOn(client, "fetchSession").mockResolvedValue(SIGNED_OUT);
    const login = vi
      .spyOn(client, "login")
      .mockRejectedValueOnce(new Error("Invalid password"))
      .mockResolvedValueOnce({ ...GUEST, guest_mode: false });
    renderApp();

    const box = await screen.findByLabelText("Password");
    fireEvent.change(box, { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid password");
    expect(fetchPhotos).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "guestpass" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(login).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByAltText("beach.jpg")).toBeInTheDocument());
    // A guest who signed in with the guest password still has no Manage.
    expect(screen.queryByText("Manage")).not.toBeInTheDocument();
  });

  it("treats an unreachable session check as not signed in", async () => {
    vi.spyOn(client, "fetchSession").mockRejectedValue(new Error("down"));
    renderApp();

    await waitFor(() => expect(screen.getByLabelText("Password")).toBeInTheDocument());
    expect(fetchPhotos).not.toHaveBeenCalled();
  });
});
