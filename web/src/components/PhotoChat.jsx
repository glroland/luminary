import { useEffect, useState } from "react";

// No model is wired up yet -- sending a message just echoes this placeholder
// so the chat UI is fully testable ahead of the real integration.
const PLACEHOLDER_REPLY = "Chat isn't connected to a model yet — this is just the chat interface for now.";

export default function PhotoChat({ photoId }) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    setMessages([]);
    setDraft("");
  }, [photoId]);

  function handleSubmit(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;

    setMessages((current) => [
      ...current,
      { role: "user", text },
      { role: "assistant", text: PLACEHOLDER_REPLY },
    ]);
    setDraft("");
  }

  return (
    <div className="photo-chat">
      <div className="photo-chat-messages">
        {messages.length === 0 ? (
          <p className="photo-chat-empty">Ask something about this photo.</p>
        ) : (
          messages.map((message, index) => (
            <div key={index} className={`photo-chat-message photo-chat-message-${message.role}`}>
              {message.text}
            </div>
          ))
        )}
      </div>
      <form className="photo-chat-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask about this photo..."
          aria-label="Chat message"
        />
        <button type="submit">Send</button>
      </form>
    </div>
  );
}
