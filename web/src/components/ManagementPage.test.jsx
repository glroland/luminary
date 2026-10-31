import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ManagementPage from "./ManagementPage.jsx";
import * as client from "../api/client.js";

const ALICE = { id: "p1", name: "Alice", has_representative_thumbnail: false, created_at: "2024-01-01T00:00:00Z" };
const BOB = { id: "p2", name: "Bob", has_representative_thumbnail: false, created_at: "2024-01-01T00:00:00Z" };

describe("ManagementPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
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
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

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
});
