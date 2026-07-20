// Nudgy chat button — the brand orb on a glossy white disc (design handoff).
export function ChatbotOrb({ size = 48 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="chatBtnGrad" gradientUnits="userSpaceOnUse" x1="18" y1="12" x2="82" y2="90">
          <stop offset="0" stopColor="#FFFFFF" />
          <stop offset="1" stopColor="#E7EAEE" />
        </linearGradient>
        <radialGradient id="chatBtnGloss" cx="0.34" cy="0.26" r="0.7">
          <stop offset="0" stopColor="#FFFFFF" stopOpacity="0.9" />
          <stop offset="0.55" stopColor="#FFFFFF" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="chatOrbGrad" gradientUnits="userSpaceOnUse" x1="34" y1="34" x2="74" y2="74">
          <stop offset="0" stopColor="#2E6698" />
          <stop offset="1" stopColor="#12314A" />
        </linearGradient>
      </defs>
      <circle cx="50" cy="50" r="47" fill="url(#chatBtnGrad)" stroke="#E2E6EB" strokeWidth="1.25" />
      <circle cx="50" cy="50" r="47" fill="url(#chatBtnGloss)" />
      <g transform="translate(28,28) scale(0.44)">
        <circle cx="50" cy="50" r="36" fill="url(#chatOrbGrad)" fillOpacity="0.16" />
        <circle cx="46" cy="47" r="18" fill="url(#chatOrbGrad)" fillOpacity="0.6" />
        <circle cx="54" cy="53" r="16" fill="url(#chatOrbGrad)" fillOpacity="0.78" />
        <circle cx="50" cy="50" r="39" stroke="url(#chatOrbGrad)" strokeWidth="4.2" fill="none" />
      </g>
    </svg>
  );
}
