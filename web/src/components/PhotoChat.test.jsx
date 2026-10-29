import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PhotoChat from "./PhotoChat.jsx";

describe("PhotoChat", () => {
  it("shows a prompt when there are no messages yet", () => {
    render(<PhotoChat photoId="1" />);
    expect(screen.getByText(/ask something about this photo/i)).toBeInTheDocument();
  });

  it("echoes the user's message and a placeholder reply, then clears the input", () => {
    render(<PhotoChat photoId="1" />);

    const input = screen.getByLabelText(/chat message/i);
    fireEvent.change(input, { target: { value: "Where was this taken?" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(screen.getByText("Where was this taken?")).toBeInTheDocument();
    expect(screen.getByText(/isn't connected to a model yet/i)).toBeInTheDocument();
    expect(input.value).toBe("");
  });

  it("does not send an empty or whitespace-only message", () => {
    render(<PhotoChat photoId="1" />);

    const input = screen.getByLabelText(/chat message/i);
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(screen.getByText(/ask something about this photo/i)).toBeInTheDocument();
  });

  it("resets the conversation when the photo changes", () => {
    const { rerender } = render(<PhotoChat photoId="1" />);

    const input = screen.getByLabelText(/chat message/i);
    fireEvent.change(input, { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(screen.getByText("Hello")).toBeInTheDocument();

    rerender(<PhotoChat photoId="2" />);
    expect(screen.queryByText("Hello")).not.toBeInTheDocument();
    expect(screen.getByText(/ask something about this photo/i)).toBeInTheDocument();
  });
});
