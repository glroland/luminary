// The Luminary wordmark, inlined (rather than an <img src="logo.svg">) so
// its colours can come from the theme tokens in styles.css -- as a
// standalone image its navy text is unreadable on the dark theme.
// assets/logo.svg is the same artwork with the light theme's colours baked
// in; keep the two in step if the mark changes.
export default function Logo() {
  return (
    <svg className="app-logo" viewBox="0 0 300 72" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Luminary">
      <g className="app-logo-rays" strokeWidth="3" strokeLinecap="round">
        <line x1="36" y1="2" x2="36" y2="10" />
        <line x1="36" y1="62" x2="36" y2="70" />
        <line x1="2" y1="36" x2="10" y2="36" />
        <line x1="62" y1="36" x2="70" y2="36" />
        <line x1="13" y1="13" x2="19" y2="19" />
        <line x1="53" y1="53" x2="59" y2="59" />
        <line x1="59" y1="13" x2="53" y2="19" />
        <line x1="19" y1="53" x2="13" y2="59" />
      </g>
      <circle className="app-logo-ring" cx="36" cy="36" r="26" fill="none" strokeWidth="5" />
      <circle className="app-logo-core" cx="36" cy="36" r="18" />
      <circle cx="32" cy="31" r="6.5" fill="#bcdcf7" opacity="0.9" />
      <text
        className="app-logo-text"
        x="86"
        y="47"
        fontFamily="'Segoe UI', Helvetica, Arial, sans-serif"
        fontSize="34"
        fontWeight="700"
        letterSpacing="0.5"
      >
        Luminary
      </text>
    </svg>
  );
}
