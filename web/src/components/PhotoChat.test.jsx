import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PhotoChat from "./PhotoChat.jsx";
import * as client from "../api/client.js";

describe("PhotoChat", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a prompt when there are no messages yet", () => {
    render(<PhotoChat photoId="1" />);
    expect(screen.getByText(/ask something about this photo/i)).toBeInTheDocument();
  });

  it("sends the message and shows the reply", async () => {
    const chatMock = vi
      .spyOn(client, "chatAboutPhoto")
      .mockResolvedValue({ reply: "It's a red square.", boxes: [] });

    render(<PhotoChat photoId="1" />);

    const input = screen.getByLabelText(/chat message/i);
    fireEvent.change(input, { target: { value: "Where was this taken?" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(screen.getByText("Where was this taken?")).toBeInTheDocument();
    expect(input.value).toBe("");

    await waitFor(() => {
      expect(screen.getByText("It's a red square.")).toBeInTheDocument();
    });

    expect(chatMock).toHaveBeenCalledWith("1", "Where was this taken?", [], expect.any(Function), null);
  });

  it("has no backend checkbox -- the model decides when to search for an object", () => {
    render(<PhotoChat photoId="1" />);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("sends prior turns as history on the next message", async () => {
    const chatMock = vi
      .spyOn(client, "chatAboutPhoto")
      .mockResolvedValueOnce({ reply: "first reply", boxes: [] })
      .mockResolvedValueOnce({ reply: "second reply", boxes: [] });

    render(<PhotoChat photoId="1" />);

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "first question" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("first reply")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "second question" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("second reply")).toBeInTheDocument());

    expect(chatMock).toHaveBeenLastCalledWith(
      "1",
      "second question",
      [
        { role: "user", text: "first question" },
        { role: "assistant", text: "first reply" },
      ],
      expect.any(Function),
      null
    );
  });

  it("shows an error message when the chat request fails", async () => {
    vi.spyOn(client, "chatAboutPhoto").mockRejectedValue(
      new Error("OpenAI chat isn't configured: set LUMINARY_OPENAI_API_KEY.")
    );

    render(<PhotoChat photoId="1" />);

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("LUMINARY_OPENAI_API_KEY");
    });
  });

  it("does not send an empty or whitespace-only message", () => {
    const chatMock = vi.spyOn(client, "chatAboutPhoto");

    render(<PhotoChat photoId="1" />);

    const input = screen.getByLabelText(/chat message/i);
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(screen.getByText(/ask something about this photo/i)).toBeInTheDocument();
    expect(chatMock).not.toHaveBeenCalled();
  });

  it("grows the assistant message in place as streamed chunks arrive", async () => {
    vi.spyOn(client, "chatAboutPhoto").mockImplementation(async (photoId, message, history, onChunk) => {
      onChunk("Hel");
      // "Thinking..." must disappear as soon as the first chunk starts the
      // assistant message, not only once the whole reply has arrived.
      expect(screen.queryByText(/thinking/i)).not.toBeInTheDocument();
      onChunk("lo, ");
      onChunk("world");
      return { reply: "Hello, world", boxes: [] };
    });

    render(<PhotoChat photoId="1" />);

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText("Hello, world")).toBeInTheDocument();
    });
    // Must be one single growing message, not three separate ones.
    expect(screen.getAllByText(/Hello, world/)).toHaveLength(1);
  });

  it("reports boxes the model's object search found via onBoxesChange", async () => {
    const boxes = [{ label: "the dog", x1: 1, y1: 2, x2: 3, y2: 4 }];
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({ reply: "found it", boxes });
    const onBoxesChange = vi.fn();

    render(<PhotoChat photoId="1" onBoxesChange={onBoxesChange} />);

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "where is the dog?" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(onBoxesChange).toHaveBeenCalledWith(boxes);
    });
  });

  it("clears boxes from the previous turn as soon as a new message is sent", async () => {
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({ reply: "a reply", boxes: [] });
    const onBoxesChange = vi.fn();

    render(<PhotoChat photoId="1" onBoxesChange={onBoxesChange} />);

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    // Cleared synchronously on submit, before the response even resolves.
    expect(onBoxesChange).toHaveBeenCalledWith([]);

    await waitFor(() => expect(screen.getByText("a reply")).toBeInTheDocument());
  });

  it("chats about the library when no photo is open", async () => {
    const found = [
      { id: "a", filename: "a.jpg", score: 0.9 },
      { id: "b", filename: "b.jpg", score: 0.6 },
      { id: "c", filename: "c.jpg", score: 0.4 },
    ];
    const chatMock = vi
      .spyOn(client, "chatAboutPhoto")
      .mockResolvedValue({ reply: "Found 3.", boxes: [], searchResults: found });
    const onSearchResults = vi.fn();
    // The list an earlier search left: sent along so the model's tools can
    // filter or truncate it.
    const previous = [{ id: "z", filename: "z.jpg", score: 1 }];

    render(<PhotoChat photoId={null} searchList={previous} onSearchResults={onSearchResults} />);

    expect(screen.getByText(/ask me to find photos/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "find beach photos" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(screen.getByText("Found 3.")).toBeInTheDocument());
    expect(chatMock).toHaveBeenCalledWith(null, "find beach photos", [], expect.any(Function), previous);
    expect(onSearchResults).toHaveBeenCalledWith(found);
  });

  it("reports an empty search, but not a reply that didn't search", async () => {
    const chatMock = vi.spyOn(client, "chatAboutPhoto");
    const onSearchResults = vi.fn();
    render(<PhotoChat photoId="1" onSearchResults={onSearchResults} />);

    chatMock.mockResolvedValueOnce({ reply: "just chatting", boxes: [], searchResults: null });
    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("just chatting")).toBeInTheDocument());
    expect(onSearchResults).not.toHaveBeenCalled();

    chatMock.mockResolvedValueOnce({ reply: "nothing found", boxes: [], searchResults: [] });
    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "find unicorns" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("nothing found")).toBeInTheDocument());
    expect(onSearchResults).toHaveBeenCalledWith([]);
  });

  it("keeps the conversation, but clears the boxes, when the photo changes", async () => {
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({ reply: "a reply", boxes: [], searchResults: null });
    const onBoxesChange = vi.fn();

    const { rerender } = render(<PhotoChat photoId="1" onBoxesChange={onBoxesChange} />);

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("a reply")).toBeInTheDocument());

    onBoxesChange.mockClear();
    await act(async () => {
      rerender(<PhotoChat photoId={null} onBoxesChange={onBoxesChange} />);
    });
    expect(screen.getByText("Hello")).toBeInTheDocument();
    expect(screen.getByText("a reply")).toBeInTheDocument();
    expect(onBoxesChange).toHaveBeenCalledWith([]);
  });
});
