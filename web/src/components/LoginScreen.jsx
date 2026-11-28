import Logo from "./Logo.jsx";
import LoginForm from "./LoginForm.jsx";

// Shown instead of the app when sign-in is required (auth on, guest mode
// off, no session).
export default function LoginScreen() {
  return (
    <div className="login-screen">
      <div className="login-card">
        <Logo />
        <h1 className="login-title">Sign in</h1>
        <LoginForm />
      </div>
    </div>
  );
}
