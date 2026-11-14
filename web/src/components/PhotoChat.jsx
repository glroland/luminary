import { useEffect, useState } from "react";
import { chatAboutPhoto } from "../api/client.js";

// `photoId` is the photo open in PhotoModal, which the model is shown and
// can be asked about -- or null when none is open, when the chat is about
// the library as a whole. Either way the model can search the library
// (see chat.py's tools); the search list it builds ({id, filename, score}
// entries, best score first) goes up through `onSearchResults`, and boxes
// it locates in the open photo through `onBoxesChange`.
//
// `searchList` is that same list coming back down: it is sent with every
// message, so the model's tools can filter or truncate an earlier search
// without the list ever being part of the conversation text.
//
// `messages` holds what the chat window lists, oldest first: the user's
// and the assistant's messages ({role, text}), and between them a row per
// tool the model called ({role: "tool", index, name, arguments, status}),
// added the moment the call starts and ticked off when it finishes.
//
// The conversation carries on across photos being opened and closed: a
// search closes the open photo to show its results, and that mustn't wipe
// the reply that announced them. Only the boxes are per-photo.
// What each of chat.py's tools is doing, in the user's terms, and the
// argument that says what it's doing it to.
const TOOL_LABELS = {
  search_photos_by_filename: ["Searching file names for", "text"],
  search_photos_semantic: ["Searching for photos of", "query"],
  search_photos_by_text: ["Searching descriptions for", "text"],
  search_photos_by_individual: ["Searching for photos of", "name"],
  search_photos_by_tag: ["Searching for photos tagged", "name"],
  list_individuals: ["Listing people"],
  list_tags: ["Listing tags"],
  search_list_stats: ["Checking the search results"],
  truncate_search_list: ["Trimming the search results below", "min_score"],
  locate_object: ["Looking in the photo for", "object"],
};

const SEARCH_MODE_NOTES = { filter: "narrowing the results", add: "adding to the results" };

export function describeToolCall({ name, arguments: args }) {
  const [label, argument] = TOOL_LABELS[name] || [name];
  const value = argument ? args?.[argument] : undefined;
  let text = label;
  if (typeof value === "string" && value.trim()) text += ` “${value.trim()}”`;
  // A score: the model writes a percentage, or a fraction of 1 (see
  // chat.py's _score_argument).
  else if (typeof value === "number") text += ` ${Math.round(value <= 1 ? value * 100 : value)}%`;
  const note = SEARCH_MODE_NOTES[args?.mode];
  return note ? `${text} (${note})` : text;
}

// The conversation as the API takes it: {role, text} turns only. Tool rows
// are display-only, and a reply a tool call split into two bubbles goes
// back as one message -- some chat templates reject two assistant turns
// in a row.
function toHistory(messages) {
  const history = [];
  for (const { role, text } of messages) {
    if (role === "tool") continue;
    const last = history[history.length - 1];
    if (role === "assistant" && last?.role === "assistant") last.text += `\n${text}`;
    else history.push({ role, text });
  }
  return history;
}

export default function PhotoChat({ photoId, searchList = null, onBoxesChange, onSearchResults }) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    onBoxesChange?.([]);
  }, [photoId]);

  async function handleSubmit(event) {
    event.preventDefault();
    const text = draft.trim();
    if (isSending || !text) return;

    const history = toHistory(messages);

    setMessages((current) => [...current, { role: "user", text }]);
    setDraft("");
    setError(null);
    setIsSending(true);
    // Clear any box(es) drawn for the previous turn immediately -- they're
    // specific to that reply, not the conversation as a whole. New ones
    // arrive with the reply if the model searched the photo for an object
    // (its locate_object tool, see chat.py) and found it.
    onBoxesChange?.([]);

    // Each piece of the reply grows the assistant message at the end of
    // the list, or starts one if the list ends in anything else -- the
    // user's message, or a tool row, so text that follows a tool call
    // gets a bubble of its own below it.
    let receivedChunk = false;
    function handleChunk(chunk) {
      receivedChunk = true;
      setMessages((current) => {
        const last = current[current.length - 1];
        if (last?.role !== "assistant") return [...current, { role: "assistant", text: chunk }];
        return [...current.slice(0, -1), { ...last, text: last.text + chunk }];
      });
    }

    // A tool call is reported twice: when the model makes it ("running",
    // which adds its row) and when it has finished (which updates that row).
    function handleTool(tool) {
      setMessages((current) => {
        const existing = current.findLastIndex(
          (message) => message.role === "tool" && message.index === tool.index && message.status === "running",
        );
        if (existing === -1) return [...current, { role: "tool", ...tool }];
        const next = [...current];
        next[existing] = { ...next[existing], status: tool.status };
        return next;
      });
    }

    try {
      const { reply, boxes, searchResults } = await chatAboutPhoto(
        photoId,
        text,
        history,
        handleChunk,
        searchList,
        handleTool,
      );
      if (!receivedChunk) {
        // A reply that came whole (a plain JSON response): no chunks
        // arrived via handleChunk, so the message was never started above.
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
  // "Thinking…" covers the waits with nothing else on show: not while the
  // reply is arriving, nor while a tool row is spinning.
  const isStreamingReply =
    isSending &&
    (lastMessage?.role === "assistant" || (lastMessage?.role === "tool" && lastMessage.status === "running"));

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
          messages.map((message, index) =>
            message.role === "tool" ? (
              // A call still "running" once the turn is over was cut off
              // by a failure; it stops spinning but is never ticked.
              <div
                key={index}
                className="photo-chat-tool"
                data-status={message.status === "running" && !isSending ? "stopped" : message.status}
              >
                <span className="photo-chat-tool-mark" aria-hidden="true" />
                {describeToolCall(message)}
              </div>
            ) : (
              <div key={index} className={`photo-chat-message photo-chat-message-${message.role}`}>
                {message.text}
              </div>
            ),
          )
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
