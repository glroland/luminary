import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { fetchSession, login as apiLogin, logout as apiLogout, setUnauthorizedHandler } from "./api/client.js";

// Who is using the app, as the API says (GET /api/auth/session). This only
// decides what the GUI shows; the API enforces the same rules itself
// (docs/security.md), so nothing here is a security boundary.
//
// With no provider -- the case in component tests -- the context is the
// "authentication off" session: everyone is an admin.
const OPEN_SESSION = {
  loading: false,
  authEnabled: false,
  guestMode: false,
  authenticated: true,
  role: "admin",
  isAdmin: true,
  displayName: "",
  // Whether a "Sign in" control makes sense (auth on and not already admin).
  canSignIn: false,
  login: async () => {},
  logout: async () => {},
};

const AuthContext = createContext(OPEN_SESSION);

function toSession(info) {
  return {
    authEnabled: Boolean(info.auth_enabled),
    guestMode: Boolean(info.guest_mode),
    authenticated: Boolean(info.authenticated),
    role: info.role ?? null,
    isAdmin: info.role === "admin",
    displayName: info.display_name ?? "",
  };
}

export function AuthProvider({ children }) {
  const [state, setState] = useState({ loading: true, ...toSession({}) });

  const refresh = useCallback(async () => {
    try {
      setState({ loading: false, ...toSession(await fetchSession()) });
    } catch {
      // The API can't be reached: show the login/error path rather than
      // pretending to be signed in.
      setState({ loading: false, ...toSession({ auth_enabled: true, authenticated: false }) });
    }
  }, []);

  useEffect(() => {
    refresh();
    // A 401 from any call means the session ended (expired, signed out
    // elsewhere): go back to asking who this is.
    setUnauthorizedHandler(refresh);
    return () => setUnauthorizedHandler(null);
  }, [refresh]);

  const login = useCallback(async (password) => {
    setState({ loading: false, ...toSession(await apiLogin(password)) });
  }, []);

  const logout = useCallback(async () => {
    setState({ loading: false, ...toSession(await apiLogout()) });
  }, []);

  const value = useMemo(
    () => ({
      ...state,
      canSignIn: state.authEnabled && !state.isAdmin,
      login,
      logout,
    }),
    [state, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
