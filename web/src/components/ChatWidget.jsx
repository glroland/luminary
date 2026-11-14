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
// The launcher's icon is a speech bubble drawn as the logo's luminaria
// on a black disc (LuminaryBubble below), in the logo's own warm palette
// -- the same in both themes, like the lantern in Logo.jsx.
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
        <LuminaryBubble />
      </button>
    </div>
  );
}

// A speech bubble made of the logo's glowing lantern paper, with its star
// cut-out, on a black disc with the candle's halo behind it. The colours
// are Logo.jsx's LanternArt palette. The halo moves via CSS (styles.css's
// .chat-launcher-halo), so the prefers-reduced-motion block there stills it.
function LuminaryBubble() {
  return (
    <svg className="chat-launcher-icon" viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <radialGradient id="chat-halo" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#ffc24a" stopOpacity="0.75" />
          <stop offset="55%" stopColor="#ff9d2e" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#ff9d2e" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="chat-paper" cx="50%" cy="82%" r="85%">
          <stop offset="0%" stopColor="#fffbe0" />
          <stop offset="30%" stopColor="#ffdc73" />
          <stop offset="70%" stopColor="#f6a534" />
          <stop offset="100%" stopColor="#d9731c" />
        </radialGradient>
      </defs>
      <circle cx="32" cy="32" r="32" fill="#000000" />
      <circle className="chat-launcher-halo" cx="32" cy="32" r="32" fill="url(#chat-halo)" />
      {/* The bubble: rounded body with its tail at the bottom left. */}
      <path
        d="M18 17 H46 a8 8 0 0 1 8 8 V37 a8 8 0 0 1 -8 8 H28 L18 54 V45 a8 8 0 0 1 -8 -8 V25 a8 8 0 0 1 8 -8 Z"
        fill="url(#chat-paper)"
        stroke="#c9701f"
        strokeWidth="0.8"
      />
      {/* The logo's star cut-out, candlelight shining through. */}
      <path d="M32 22 Q33.1 29.9 41 31 Q33.1 32.1 32 40 Q30.9 32.1 23 31 Q30.9 29.9 32 22 Z" fill="#fffdf0" />
    </svg>
  );
}
