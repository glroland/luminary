import { useState } from "react";
import { useAuth } from "../auth.jsx";

// One password box. Whether the password makes an admin or a guest is the
// server's decision (docs/security.md); this only submits it.
export default function LoginForm({ onDone = () => {}, autoFocus = true }) {
  const { login } = useAuth();
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(password);
      setPassword("");
      onDone();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="login-form" onSubmit={handleSubmit}>
      <label htmlFor="login-password">Password</label>
      <input
        id="login-password"
        type="password"
        autoComplete="current-password"
        autoFocus={autoFocus}
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <button type="submit" disabled={!password || busy}>
        Sign in
      </button>
      {error && (
        <p role="alert" className="login-error">
          {error}
        </p>
      )}
    </form>
  );
}
