import { useEffect, useRef, useState } from "react";
import { chatAboutPhoto } from "../api/client.js";

export default function PhotoChat({ photoId, onBoxesChange }) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [useFlorence, setUseFlorence] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState(null);
  // Index of the assistant message currently being grown by streamed
  // chunks, or null before the first chunk of this turn has arrived (or
  // for a non-streaming reply, for the whole turn).
  const streamingIndexRef = useRef(null);

  useEffect(() => {
    setMessages([]);
    setDraft("");
    setError(null);
    setIsSending(false);
    streamingIndexRef.current = null;
    onBoxesChange?.([]);
  }, [photoId]);

  async function handleSubmit(event) {
    event.preventDefault();
    const text = draft.trim();
    const backend = useFlorence ? "florence" : "openai";
    // Florence-2 treats an empty message as "just describe the photo"
    // (see chat.py) rather than a question -- only the openai backend needs
    // a non-empty message to be worth sending.
    if (isSending || (!text && backend !== "florence")) return;

    const history = messages.map((message) => ({ role: message.role, text: message.text }));

    setMessages((current) => [...current, { role: "user", text: text || "(describe this photo)" }]);
    setDraft("");
    setError(null);
    setIsSending(true);
    streamingIndexRef.current = null;
    // Clear any box(es) drawn for the previous turn immediately -- they're
    // specific to that reply, not the conversation as a whole.
    onBoxesChange?.([]);

    function handleChunk(chunk) {
      setMessages((current) => {
        if (streamingIndexRef.current === null) {
          streamingIndexRef.current = current.length;
          return [...current, { role: "assistant", text: chunk }];
        }
        const next = [...current];
        const index = streamingIndexRef.current;
        next[index] = { ...next[index], text: next[index].text + chunk };
        return next;
      });
    }

    try {
      const { reply, boxes } = await chatAboutPhoto(photoId, backend, text, history, handleChunk);
      if (streamingIndexRef.current === null) {
        // A plain (non-streaming) reply: no chunks arrived via handleChunk,
        // so the message was never started above -- append it now.
        setMessages((current) => [...current, { role: "assistant", text: reply }]);
      }
      onBoxesChange?.(boxes);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSending(false);
    }
  }

  const lastMessage = messages[messages.length - 1];
  const isStreamingReply = isSending && lastMessage?.role === "assistant";

  return (
    <div className="photo-chat">
      <label className="photo-chat-backend">
        <input
          type="checkbox"
          checked={useFlorence}
          onChange={(event) => setUseFlorence(event.target.checked)}
        />
        Natural Language Object Detection
      </label>
      {useFlorence && (
        <p className="photo-chat-hint">
          Locates objects from a short phrase (e.g. "the dog") using the local Florence-2 model — it
          doesn't answer questions. Leave the message blank for a general description instead.
        </p>
      )}

      <div className="photo-chat-messages">
        {messages.length === 0 ? (
          !useFlorence && <p className="photo-chat-empty">Ask something about this photo.</p>
        ) : (
          messages.map((message, index) => (
            <div key={index} className={`photo-chat-message photo-chat-message-${message.role}`}>
              {message.text}
            </div>
          ))
        )}
        {isSending && !isStreamingReply && <p className="photo-chat-pending">Thinking…</p>}
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
          placeholder={useFlorence ? "What object are you looking for?" : "Ask about this photo..."}
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
