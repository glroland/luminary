import { useEffect, useRef, useState } from "react";
import { chatAboutPhoto } from "../api/client.js";

// `photoId` is the photo open in PhotoModal, which the model is shown and
// can be asked about -- or null when none is open, when the chat is about
// the library as a whole. Either way the model can search the library
// (see chat.py's tools); the ids it finds go up through `onSearchResults`,
// and boxes it locates in the open photo through `onBoxesChange`.
//
// The conversation carries on across photos being opened and closed: a
// search closes the open photo to show its results, and that mustn't wipe
// the reply that announced them. Only the boxes are per-photo.
export default function PhotoChat({ photoId, onBoxesChange, onSearchResults }) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState(null);
  // Index of the assistant message currently being grown by streamed
  // chunks, or null before the first chunk of this turn has arrived (or
  // for a non-streaming reply, for the whole turn).
  const streamingIndexRef = useRef(null);

  useEffect(() => {
    onBoxesChange?.([]);
  }, [photoId]);

  async function handleSubmit(event) {
    event.preventDefault();
    const text = draft.trim();
    if (isSending || !text) return;

    const history = messages.map((message) => ({ role: message.role, text: message.text }));

    setMessages((current) => [...current, { role: "user", text }]);
    setDraft("");
    setError(null);
    setIsSending(true);
    streamingIndexRef.current = null;
    // Clear any box(es) drawn for the previous turn immediately -- they're
    // specific to that reply, not the conversation as a whole. New ones
    // arrive with the reply if the model searched the photo for an object
    // (its locate_object tool, see chat.py) and found it.
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
      const { reply, boxes, searchResults } = await chatAboutPhoto(photoId, text, history, handleChunk);
      if (streamingIndexRef.current === null) {
        // A plain (non-streaming) reply: no chunks arrived via handleChunk,
        // so the message was never started above -- append it now.
        setMessages((current) => [...current, { role: "assistant", text: reply }]);
      }
      onBoxesChange?.(boxes);
      if (searchResults) onSearchResults?.(searchResults);
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
      <div className="photo-chat-messages">
        {messages.length === 0 ? (
          <p className="photo-chat-empty">
            {photoId
              ? "Ask something about this photo, or ask me to find photos."
              : "Ask me to find photos — by what's in them, a name or place, or a file name."}
          </p>
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
          placeholder={photoId ? "Ask about this photo, or search..." : "Search your photos..."}
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
