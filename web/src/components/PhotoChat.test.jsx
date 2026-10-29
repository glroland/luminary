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

  it("sends the message to the openai backend by default and shows the reply", async () => {
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

    expect(chatMock).toHaveBeenCalledWith("1", "openai", "Where was this taken?", [], expect.any(Function));
  });

  it("sends to the florence backend when the checkbox is checked", async () => {
    const chatMock = vi
      .spyOn(client, "chatAboutPhoto")
      .mockResolvedValue({ reply: "A detailed caption.", boxes: [] });

    render(<PhotoChat photoId="1" />);

    fireEvent.click(screen.getByRole("checkbox", { name: /object detection/i }));
    expect(screen.getByText(/doesn't answer questions/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/chat message/i)).toHaveAttribute(
      "placeholder",
      "What object are you looking for?"
    );

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(chatMock).toHaveBeenCalledWith("1", "florence", "hi", [], expect.any(Function));
    });
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
      "openai",
      "second question",
      [
        { role: "user", text: "first question" },
        { role: "assistant", text: "first reply" },
      ],
      expect.any(Function)
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

  it("does not send an empty or whitespace-only message to openai", () => {
    const chatMock = vi.spyOn(client, "chatAboutPhoto");

    render(<PhotoChat photoId="1" />);

    const input = screen.getByLabelText(/chat message/i);
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(screen.getByText(/ask something about this photo/i)).toBeInTheDocument();
    expect(chatMock).not.toHaveBeenCalled();
  });

  it("sends an empty message to florence for a general description", async () => {
    const chatMock = vi
      .spyOn(client, "chatAboutPhoto")
      .mockResolvedValue({ reply: "a detailed caption", boxes: [] });

    render(<PhotoChat photoId="1" />);

    fireEvent.click(screen.getByRole("checkbox", { name: /object detection/i }));
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(screen.getByText("(describe this photo)")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("a detailed caption")).toBeInTheDocument();
    });

    expect(chatMock).toHaveBeenCalledWith("1", "florence", "", [], expect.any(Function));
  });

  it("grows the assistant message in place as streamed chunks arrive", async () => {
    vi.spyOn(client, "chatAboutPhoto").mockImplementation(async (photoId, backend, message, history, onChunk) => {
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

  it("reports boxes from a florence grounding reply via onBoxesChange", async () => {
    const boxes = [{ label: "the dog", x1: 1, y1: 2, x2: 3, y2: 4 }];
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({ reply: "found it", boxes });
    const onBoxesChange = vi.fn();

    render(<PhotoChat photoId="1" onBoxesChange={onBoxesChange} />);

    fireEvent.click(screen.getByRole("checkbox", { name: /object detection/i }));
    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "the dog" } });
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

  it("resets the conversation when the photo changes", async () => {
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue({ reply: "a reply", boxes: [] });

    const { rerender } = render(<PhotoChat photoId="1" />);

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("a reply")).toBeInTheDocument());

    await act(async () => {
      rerender(<PhotoChat photoId="2" />);
    });
    expect(screen.queryByText("Hello")).not.toBeInTheDocument();
    expect(screen.getByText(/ask something about this photo/i)).toBeInTheDocument();
  });
});
