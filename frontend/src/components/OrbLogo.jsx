// Nudgy brand mark — layered glass orb (from the design handoff).
export function OrbLogo({ size = 48 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="orbGrad" gradientUnits="userSpaceOnUse" x1="15" y1="15" x2="85" y2="85">
          <stop offset="0" stopColor="#2E6698" />
          <stop offset="1" stopColor="#12314A" />
        </linearGradient>
      </defs>
      <circle cx="50" cy="50" r="36" fill="url(#orbGrad)" fillOpacity="0.14" />
      <circle cx="46" cy="47" r="18" fill="url(#orbGrad)" fillOpacity="0.55" />
      <circle cx="54" cy="53" r="16" fill="url(#orbGrad)" fillOpacity="0.72" />
      <circle cx="50" cy="50" r="39" stroke="url(#orbGrad)" strokeWidth="4.2" fill="none" />
    </svg>
  );
}
