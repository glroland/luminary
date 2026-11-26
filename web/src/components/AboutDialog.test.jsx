import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client.js";
import AboutDialog from "./AboutDialog.jsx";

const ABOUT = {
  version: null,
  year: 2031,
  photos_source: "smb://nas/photos",
  chat_model: "my-model",
  chat_endpoint: null,
  database_name: "luminary",
  database_host: "db:5432",
};

describe("AboutDialog", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows development mode, metadata and the copyright", async () => {
    vi.spyOn(client, "fetchAbout").mockResolvedValue(ABOUT);
    render(<AboutDialog onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText("Version: Development mode")).toBeInTheDocument());
    expect(screen.getByText("smb://nas/photos")).toBeInTheDocument();
    expect(screen.getByText("my-model")).toBeInTheDocument();
    expect(screen.getByText("luminary")).toBeInTheDocument();
    expect(
      screen.getByText("© 2031 Lee Roland. All rights reserved."),
    ).toBeInTheDocument();
  });

  it("shows the build version and closes on Escape", async () => {
    vi.spyOn(client, "fetchAbout").mockResolvedValue({ ...ABOUT, version: "42" });
    const onClose = vi.fn();
    render(<AboutDialog onClose={onClose} />);

    await waitFor(() => expect(screen.getByText("Version: 42")).toBeInTheDocument());
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
