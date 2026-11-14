import { useState } from "react";
import PhotoChat from "./PhotoChat.jsx";

// The floating chat: a launcher button pinned to the bottom-right corner
// of the viewport, and the popup it opens. Rendered once by App.jsx, above
// everything else (the photo modal included), so it's reachable from any
// view. `photo` is whichever photo is open in PhotoModal, or null -- the
// chat API is per-photo (POST /api/photos/{id}/chat), so that open photo
// is what the conversation is about. `onBoxesChange` hands the florence
// backend's located boxes up to App.jsx, which passes them to PhotoModal
// to draw over the image.
//
// PhotoChat stays mounted while the popup is closed (the popup is only
// hidden), so closing and reopening it doesn't lose the conversation.
export default function ChatWidget({ photo, onBoxesChange }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="chat-widget">
      <section className="chat-popup" aria-label="Chat" hidden={!open}>
        <header className="chat-popup-header">
          <div className="chat-popup-heading">
            <h2>Chat</h2>
            <p className="chat-popup-context" title={photo?.filename}>
              {photo ? `About ${photo.filename.split("/").pop()}` : "No photo open"}
            </p>
          </div>
          <button type="button" className="chat-popup-close" aria-label="Close chat" onClick={() => setOpen(false)}>
            ×
          </button>
        </header>
        <PhotoChat photoId={photo?.id ?? null} onBoxesChange={onBoxesChange} />
      </section>

      <button
        type="button"
        className="chat-launcher"
        aria-label={open ? "Close chat" : "Open chat"}
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
          <path
            d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4A2.5 2.5 0 0 1 4 13.5z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  );
}
