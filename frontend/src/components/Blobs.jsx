// Large blurred color blobs floating behind the glass — per-page palettes,
// ported from the prototype's blobSets.

const SETS = {
  signin: [
    ["#2A9D8F", 520, -180, -140, null, null, 0.5],
    ["#BCA9C9", 400, -120, null, -80, null, 0.4],
    ["#D95D39", 440, null, null, 120, -160, 0.4],
    ["#DCA744", 360, null, 160, null, -120, 0.36],
  ],
  connect: [
    ["#2B5B84", 500, -160, null, -120, null, 0.42],
    ["#2A9D8F", 400, null, -100, null, -140, 0.4],
    ["#CBA39C", 320, null, null, 200, 40, 0.3],
  ],
  home: [
    ["#2A9D8F", 480, -140, -100, null, null, 0.55],
    ["#D95D39", 340, 80, null, -100, null, 0.42],
    ["#BCA9C9", 220, 20, null, 140, null, 0.35],
    ["#DCA744", 380, null, 460, null, -120, 0.4],
  ],
  calendar: [
    ["#2A9D8F", 400, -120, 360, null, null, 0.42],
    ["#D95D39", 300, null, null, 60, -80, 0.4],
    ["#BCA9C9", 220, 220, 40, null, null, 0.32],
  ],
  activity: [
    ["#CBA39C", 380, -100, 400, null, null, 0.36],
    ["#2B5B84", 320, null, -60, null, -100, 0.3],
  ],
  polls: [
    ["#DCA744", 400, -120, null, 80, null, 0.38],
    ["#2A9D8F", 300, null, 120, null, -80, 0.34],
  ],
  settings: [
    ["#E68E36", 420, -140, 220, null, null, 0.36],
    ["#2A9D8F", 340, null, null, 100, -100, 0.3],
  ],
};

export default function Blobs({ page }) {
  const set = SETS[page] || SETS.home;
  return set.map(([c, size, top, left, right, bottom, op], i) => (
    <div
      key={i}
      style={{
        position: "absolute",
        borderRadius: "50%",
        filter: "blur(70px)",
        pointerEvents: "none",
        width: size,
        height: size,
        background: `color-mix(in srgb, ${c} 36%, #F7F1E6)`,
        ...(top != null ? { top } : {}),
        ...(left != null ? { left } : {}),
        ...(right != null ? { right } : {}),
        ...(bottom != null ? { bottom } : {}),
        opacity: op,
      }}
    />
  ));
}
