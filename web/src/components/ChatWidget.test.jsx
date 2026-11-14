import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatWidget from "./ChatWidget.jsx";
import * as client from "../api/client.js";

const PHOTO = { id: "p1", filename: "vacation/2019/beach.jpg" };

describe("ChatWidget", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the popup hidden until the launcher is clicked, and hides it again on close", () => {
    render(<ChatWidget photo={null} />);

    expect(screen.queryByRole("region", { name: "Chat" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /open chat/i }));
    expect(screen.getByRole("region", { name: "Chat" })).toBeVisible();

    fireEvent.click(screen.getAllByRole("button", { name: /close chat/i })[0]);
    expect(screen.queryByRole("region", { name: "Chat" })).not.toBeInTheDocument();
  });

  it("is about the library when no photo is open", () => {
    render(<ChatWidget photo={null} />);
    fireEvent.click(screen.getByRole("button", { name: /open chat/i }));

    expect(screen.getByText("Your photo library")).toBeInTheDocument();
    // Still usable: with no photo open the chat searches the library.
    expect(screen.getByLabelText(/chat message/i)).toBeEnabled();
  });

  it("chats about the open photo and hands located boxes up", async () => {
    const boxes = [{ label: "dog", x1: 1, y1: 2, x2: 3, y2: 4 }];
    const chatMock = vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({ reply: "Found it.", boxes });
    const onBoxesChange = vi.fn();

    render(<ChatWidget photo={PHOTO} onBoxesChange={onBoxesChange} />);
    fireEvent.click(screen.getByRole("button", { name: /open chat/i }));

    expect(screen.getByText("About beach.jpg")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "the dog" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(screen.getByText("Found it.")).toBeInTheDocument());
    expect(chatMock).toHaveBeenCalledWith("p1", "the dog", [], expect.any(Function), null, expect.any(Function));
    expect(onBoxesChange).toHaveBeenLastCalledWith(boxes);
  });

  it("keeps the conversation when the popup is closed and reopened", async () => {
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({ reply: "a reply", boxes: [] });

    render(<ChatWidget photo={PHOTO} />);
    fireEvent.click(screen.getByRole("button", { name: /open chat/i }));
    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("a reply")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: /close chat/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: /open chat/i }));

    expect(screen.getByText("a reply")).toBeVisible();
  });
});
