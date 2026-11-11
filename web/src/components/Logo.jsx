// The Luminary mark and wordmark: a luminaria -- a paper-bag lantern with a
// candle inside, light spilling through a star cut into its face -- next
// to the name, lettered as if it were alight.
//
// Inline SVG rather than an image file so the wordmark's colours can come
// from the theme tokens (styles.css's --logo-* custom properties); the
// lantern itself is the same warm palette in both themes. LanternArt is
// shared artwork: public/favicon.svg is a hand-kept copy of it (a favicon
// can't import a component), so change both together.
export function LanternArt() {
  return (
    <>
      <defs>
        <radialGradient id="logo-halo" cx="50%" cy="55%" r="50%">
          <stop offset="0%" stopColor="#ffc24a" stopOpacity="0.75" />
          <stop offset="55%" stopColor="#ff9d2e" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#ff9d2e" stopOpacity="0" />
        </radialGradient>
        {/* Brightest low in the bag, where the candle sits. */}
        <radialGradient id="logo-paper" cx="50%" cy="82%" r="85%">
          <stop offset="0%" stopColor="#fffbe0" />
          <stop offset="30%" stopColor="#ffdc73" />
          <stop offset="70%" stopColor="#f6a534" />
          <stop offset="100%" stopColor="#d9731c" />
        </radialGradient>
        <linearGradient id="logo-paper-side" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#a8500f" />
          <stop offset="100%" stopColor="#d98324" />
        </linearGradient>
      </defs>
      <circle className="app-logo-halo" cx="36" cy="46" r="36" fill="url(#logo-halo)" />
      <ellipse cx="38" cy="73.5" rx="27" ry="3.2" fill="#ffb347" opacity="0.4" />
      {/* The bag's side panel, then its front face with a pinked top edge. */}
      <path d="M56 22 L62 18.5 L62 69 L56 73 Z" fill="url(#logo-paper-side)" />
      <path
        d="M16 22 L20 17 L24 22 L28 17 L32 22 L36 17 L40 22 L44 17 L48 22 L52 17 L56 22 L56 73 L16 73 Z"
        fill="url(#logo-paper)"
      />
      {/* A crease down the front, as on a folded paper bag. */}
      <path d="M36 22 L36 73" stroke="#c9701f" strokeWidth="0.6" opacity="0.35" />
      {/* Cut-outs the candlelight shines through. */}
      <g fill="#fffdf0">
        <path d="M36 31 Q37.6 42.4 49 44 Q37.6 45.6 36 57 Q34.4 45.6 23 44 Q34.4 42.4 36 31 Z" />
        <circle cx="24" cy="30" r="1.7" />
        <circle cx="48" cy="30" r="1.7" />
        <circle cx="24" cy="61" r="1.7" />
        <circle cx="48" cy="61" r="1.7" />
      </g>
    </>
  );
}

// Shared by the wordmark's two layers so they sit exactly on top of each
// other. The face is the same system UI one as the rest of the app
// (styles.css's :root font-family), set heavy and slightly tight.
//
// y is the text baseline, chosen so the capital "L" is vertically centred
// on the lantern: the bag runs from y=17 to y=73 (centre 45) and this
// face's cap height is roughly 0.72em, i.e. ~29 units at this size, so
// the baseline sits half of that below centre. Re-derive it if either the
// bag or the font size changes.
const WORDMARK_PROPS = {
  x: 78,
  y: 59.5,
  fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
  fontSize: 40,
  fontWeight: 700,
  letterSpacing: -0.8,
};

export default function Logo() {
  // The flames move via SMIL <animate>, which CSS can't switch off the way
  // styles.css's prefers-reduced-motion block does for everything else --
  // so it's simply not rendered for someone who has asked for less motion
  // (the wordmark keeps its fiery look, it just holds still).
  const still =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return (
    <svg className="app-logo" viewBox="0 0 252 80" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Luminary">
      <defs>
        {/* Hot at the base of the letters, cooling toward their tops. */}
        <linearGradient id="logo-wordmark" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" className="app-logo-text-from" />
          <stop offset="100%" className="app-logo-text-to" />
        </linearGradient>
        <linearGradient id="logo-flame-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ff3d0a" />
          <stop offset="100%" stopColor="#ffb11f" />
        </linearGradient>
        {/* Turns a copy of the lettering into flames: noise pushes its
            edges around (more vertically than horizontally, so it licks
            upward), then a slight blur softens them. The filter region
            is oversized because the displaced shapes leave the text's
            own box. */}
        <filter id="logo-flames" x="-10%" y="-60%" width="120%" height="220%">
          <feTurbulence type="fractalNoise" baseFrequency="0.04 0.11" numOctaves="2" seed="7" result="noise">
            {!still && (
              <animate
                attributeName="baseFrequency"
                dur="6s"
                values="0.04 0.11;0.055 0.15;0.04 0.11"
                repeatCount="indefinite"
              />
            )}
          </feTurbulence>
          <feDisplacementMap in="SourceGraphic" in2="noise" scale="11" xChannelSelector="R" yChannelSelector="G" />
          <feGaussianBlur stdDeviation="0.9" />
        </filter>
      </defs>
      <LanternArt />
      {/* Flame layer first (behind, nudged up so it rises off the tops of
          the letters), then the crisp, readable lettering over it. The
          flame copy is aria-hidden; the <svg>'s aria-label names the logo. */}
      <text
        {...WORDMARK_PROPS}
        className="app-logo-flames"
        fill="url(#logo-flame-fill)"
        filter="url(#logo-flames)"
        transform="translate(0 -3.5)"
        aria-hidden="true"
      >
        Luminary
      </text>
      <text {...WORDMARK_PROPS} className="app-logo-text" fill="url(#logo-wordmark)">
        Luminary
      </text>
    </svg>
  );
}
