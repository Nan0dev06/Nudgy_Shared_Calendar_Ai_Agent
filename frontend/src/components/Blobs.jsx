// Large blurred color blobs floating behind the glass — every page gets the
// same seven blob "slots", and each page nudges their color, position, size
// and opacity. Because the slots persist across pages, CSS transitions morph
// one page's ambience into the next instead of snapping — and each blob also
// drifts slowly on its own (blobDrift) so the background never sits still.

// [color, size, x%, y%, opacity] per slot — faint and spread out on purpose.
// Slots 0-3 are the big anchors; 4-6 are smaller accents that add density.
const SETS = {
  signin: [
    ["#2A9D8F", 520, 8, 4, 0.42],
    ["#BCA9C9", 400, 88, 10, 0.34],
    ["#D95D39", 440, 84, 88, 0.3],
    ["#DCA744", 360, 18, 92, 0.28],
    ["#2B5B84", 260, 50, 40, 0.16],
    ["#CBA39C", 300, 30, 60, 0.18],
    ["#E68E36", 220, 65, 15, 0.14],
  ],
  connect: [
    ["#2B5B84", 500, 6, 14, 0.36],
    ["#2A9D8F", 400, 90, 6, 0.3],
    ["#CBA39C", 340, 78, 90, 0.26],
    ["#DCA744", 300, 24, 96, 0.22],
    ["#BCA9C9", 260, 55, 30, 0.16],
    ["#D95D39", 240, 40, 70, 0.14],
    ["#2A9D8F", 200, 15, 55, 0.14],
  ],
  home: [
    ["#2A9D8F", 500, 6, 6, 0.42],
    ["#D95D39", 380, 92, 20, 0.32],
    ["#BCA9C9", 300, 80, 88, 0.28],
    ["#DCA744", 420, 34, 96, 0.32],
    ["#2B5B84", 240, 60, 45, 0.16],
    ["#CBA39C", 280, 12, 60, 0.2],
    ["#E68E36", 200, 72, 8, 0.14],
  ],
  calendar: [
    ["#2A9D8F", 440, 28, 2, 0.34],
    ["#D95D39", 340, 94, 74, 0.3],
    ["#BCA9C9", 280, 8, 66, 0.26],
    ["#DCA744", 320, 68, 98, 0.22],
    ["#2B5B84", 260, 88, 30, 0.18],
    ["#CBA39C", 220, 45, 50, 0.14],
    ["#2A9D8F", 200, 10, 30, 0.14],
  ],
  places: [
    ["#DCA744", 460, 12, 8, 0.34],
    ["#2A9D8F", 380, 90, 30, 0.3],
    ["#CBA39C", 320, 76, 92, 0.28],
    ["#D95D39", 300, 20, 90, 0.24],
    ["#BCA9C9", 260, 55, 20, 0.18],
    ["#2B5B84", 240, 40, 60, 0.16],
    ["#E68E36", 220, 92, 70, 0.16],
  ],
  activity: [
    ["#CBA39C", 420, 30, 8, 0.3],
    ["#2B5B84", 360, 88, 84, 0.26],
    ["#2A9D8F", 280, 6, 80, 0.22],
    ["#BCA9C9", 320, 70, 4, 0.2],
    ["#DCA744", 240, 50, 55, 0.16],
    ["#D95D39", 220, 15, 40, 0.14],
    ["#2A9D8F", 200, 90, 40, 0.12],
  ],
  polls: [
    ["#DCA744", 440, 6, 26, 0.32],
    ["#2A9D8F", 340, 90, 78, 0.28],
    ["#D95D39", 280, 82, 8, 0.24],
    ["#BCA9C9", 320, 30, 96, 0.22],
    ["#2B5B84", 260, 55, 40, 0.16],
    ["#CBA39C", 220, 12, 70, 0.16],
    ["#E68E36", 200, 70, 55, 0.12],
  ],
  settings: [
    ["#E68E36", 440, 16, 12, 0.3],
    ["#2A9D8F", 360, 90, 82, 0.26],
    ["#2B5B84", 300, 6, 88, 0.22],
    ["#CBA39C", 320, 76, 4, 0.2],
    ["#BCA9C9", 240, 45, 45, 0.16],
    ["#DCA744", 220, 60, 70, 0.14],
    ["#D95D39", 200, 30, 30, 0.12],
  ],
};

// per-slot drift rhythm — co-prime-ish durations so the motion never loops in
// sync; negative delays start each blob mid-cycle
const DRIFT = [
  [26, 0], [31, -8], [23, -15], [37, -4], [29, -20], [21, -11], [34, -27],
];

export default function Blobs({ page }) {
  const set = SETS[page] || SETS.home;
  return set.map(([c, size, x, y, op], i) => (
    <div
      key={i}
      style={{
        position: "absolute",
        borderRadius: "50%",
        filter: "blur(80px)",
        pointerEvents: "none",
        width: size,
        height: size,
        left: `${x}%`,
        top: `${y}%`,
        transform: "translate(-50%, -50%)",
        background: `color-mix(in srgb, ${c} 34%, #F7F1E6)`,
        opacity: op,
        animation: `blobDrift ${DRIFT[i % DRIFT.length][0]}s ease-in-out infinite`,
        animationDelay: `${DRIFT[i % DRIFT.length][1]}s`,
        transition:
          "left 1.1s cubic-bezier(.4,0,.2,1), top 1.1s cubic-bezier(.4,0,.2,1), width 1.1s cubic-bezier(.4,0,.2,1), height 1.1s cubic-bezier(.4,0,.2,1), background 1.1s linear, opacity 1.1s linear",
      }}
    />
  ));
}
