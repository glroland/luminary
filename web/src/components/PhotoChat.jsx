import { useEffect, useState } from "react";
import { chatAboutPhoto } from "../api/client.js";

export default function PhotoChat({ photoId }) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [useFlorence, setUseFlorence] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setMessages([]);
    setDraft("");
    setError(null);
    setIsSending(false);
  }, [photoId]);

  async function handleSubmit(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || isSending) return;

    const backend = useFlorence ? "florence" : "openai";
    const history = messages.map((message) => ({ role: message.role, text: message.text }));

    setMessages((current) => [...current, { role: "user", text }]);
    setDraft("");
    setError(null);
    setIsSending(true);

    try {
      const reply = await chatAboutPhoto(photoId, backend, text, history);
      setMessages((current) => [...current, { role: "assistant", text: reply }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSending(false);
    }
  }

  return (
    <div className="photo-chat">
      <label className="photo-chat-backend">
        <input
          type="checkbox"
          checked={useFlorence}
          onChange={(event) => setUseFlorence(event.target.checked)}
        />
        Use local Florence-2 model (unchecked = OpenAI)
      </label>
      {useFlorence && (
        <p className="photo-chat-hint">
          Florence-2 always returns a description of the photo — it doesn't answer specific questions.
        </p>
      )}

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
        {isSending && <p className="photo-chat-pending">Thinking…</p>}
      </div>

      {error && (
        <p className="photo-chat-error" role="alert">
          {error}
        </p>
      )}

      <form className="photo-chat-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask about this photo..."
          aria-label="Chat message"
          disabled={isSending}
        />
        <button type="submit" disabled={isSending}>
          Send
        </button>
      </form>
    </div>
  );
}
