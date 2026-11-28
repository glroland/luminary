import { useEffect } from "react";
import LoginForm from "./LoginForm.jsx";

// "Sign in" from the header, for a guest who wants to be an admin.
export default function LoginDialog({ onClose }) {
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="about-backdrop" onClick={onClose}>
      <div
        className="about-dialog login-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="login-title"
        onClick={(event) => event.stopPropagation()}
      >
        <button type="button" className="about-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2 id="login-title" className="login-title">
          Sign in
        </h2>
        <LoginForm onDone={onClose} />
      </div>
    </div>
  );
}
