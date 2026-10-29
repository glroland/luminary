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
    const chatMock = vi.spyOn(client, "chatAboutPhoto").mockResolvedValue("It's a red square.");

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
    const chatMock = vi.spyOn(client, "chatAboutPhoto").mockResolvedValue("A detailed caption.");

    render(<PhotoChat photoId="1" />);

    fireEvent.click(screen.getByRole("checkbox", { name: /use local florence-2 model/i }));
    expect(screen.getByText(/doesn't answer specific questions/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(chatMock).toHaveBeenCalledWith("1", "florence", "hi", [], expect.any(Function));
    });
  });

  it("sends prior turns as history on the next message", async () => {
    const chatMock = vi
      .spyOn(client, "chatAboutPhoto")
      .mockResolvedValueOnce("first reply")
      .mockResolvedValueOnce("second reply");

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

  it("does not send an empty or whitespace-only message", () => {
    const chatMock = vi.spyOn(client, "chatAboutPhoto");

    render(<PhotoChat photoId="1" />);

    const input = screen.getByLabelText(/chat message/i);
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(screen.getByText(/ask something about this photo/i)).toBeInTheDocument();
    expect(chatMock).not.toHaveBeenCalled();
  });

  it("resets the conversation when the photo changes", async () => {
    vi.spyOn(client, "chatAboutPhoto").mockResolvedValue("a reply");

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
