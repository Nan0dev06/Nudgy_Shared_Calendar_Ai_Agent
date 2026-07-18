// Style recipes ported 1:1 from the design prototype.
// These are the binding "liquid glass" formulas — do not tweak values.

export const INK = "#2D2D2D";
export const SAGE = "#2A9D8F";
export const SLATE = "#2B5B84";
export const TERRACOTTA = "#D95D39";
export const ROSE = "#CBA39C";
export const MUSTARD = "#DCA744";
export const LILAC = "#BCA9C9";
export const AMBER = "#E68E36";
export const NEUTRAL = "#8A8A8A";

export const orbGradient = (size) =>
  `radial-gradient(${size}px ${size}px at 32% 26%, rgba(255,255,255,.55), rgba(0,0,0,0) 60%), linear-gradient(145deg, #2A9D8F, #2B5B84)`;

export function glass(r, blur = 26) {
  return {
    background:
      "linear-gradient(150deg, rgba(255,251,244,.64), rgba(248,238,224,.38))",
    backdropFilter: `blur(${blur}px)`,
    WebkitBackdropFilter: `blur(${blur}px)`,
    border: "1px solid rgba(200,182,155,.26)",
    borderTop: "1px solid rgba(255,255,255,.85)",
    boxShadow:
      "0 1px 2px rgba(96,78,54,.07), 0 10px 22px rgba(96,78,54,.10), 0 30px 54px -18px rgba(96,78,54,.18)",
    borderRadius: r + "px",
  };
}

export function heavy(r) {
  return {
    background:
      "linear-gradient(155deg, rgba(255,251,244,.66), rgba(248,238,224,.4))",
    backdropFilter: "blur(34px)",
    WebkitBackdropFilter: "blur(34px)",
    border: "1px solid rgba(255,255,255,.6)",
    borderTop: "1px solid rgba(255,255,255,.88)",
    boxShadow:
      "0 1px 0 rgba(255,255,255,.5) inset, 0 24px 54px rgba(96,78,54,.22)",
    borderRadius: r + "px",
  };
}

export function gpill(sm) {
  return {
    borderRadius: "999px",
    padding: sm ? "7px 15px" : "11px 18px",
    fontSize: sm ? "12.5px" : "14px",
    fontWeight: 600,
    whiteSpace: "nowrap",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "7px",
    cursor: "pointer",
    background:
      "linear-gradient(160deg, rgba(255,255,255,.6), rgba(255,255,255,.32))",
    border: "1px solid rgba(255,255,255,.7)",
    boxShadow:
      "0 1px 0 rgba(255,255,255,.7) inset, 0 8px 20px rgba(96,78,54,.10)",
    color: "#2D2D2D",
    transition: "all .18s",
    userSelect: "none",
  };
}

export function dpill(sm) {
  return {
    ...gpill(sm),
    background: "linear-gradient(160deg, rgba(64,60,50,.92), rgba(45,45,45,.84))",
    color: "#F7F2EA",
    border: "1px solid rgba(255,255,255,.2)",
    boxShadow:
      "0 1px 0 rgba(255,255,255,.18) inset, 0 10px 22px rgba(45,38,28,.28)",
  };
}

export const sagePill = (sm) => ({
  ...dpill(sm),
  background: "linear-gradient(160deg, #2A9D8F, #237c72)",
});

export const outlinePill = (sm) => ({
  ...gpill(sm),
  border: "1.4px solid #2A9D8F",
  color: "#2A9D8F",
  background: "rgba(255,253,247,.4)",
});

export const dashPill = (sm) => ({
  ...gpill(sm),
  borderStyle: "dashed",
  color: "#a09889",
  background: "transparent",
  boxShadow: "none",
});

// Person-tinted glass event block
export function evBlock(tint, r) {
  return {
    borderRadius: r + "px",
    padding: "14px 15px 13px",
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    cursor: "pointer",
    position: "relative",
    background: `linear-gradient(135deg, color-mix(in srgb, ${tint} 20%, rgba(255,251,244,.62)), rgba(250,242,231,.42))`,
    backdropFilter: "blur(20px)",
    WebkitBackdropFilter: "blur(20px)",
    border: "1px solid rgba(255,255,255,.6)",
    borderTop: "1px solid rgba(255,255,255,.85)",
    boxShadow: "0 1px 2px rgba(96,78,54,.06), 0 8px 18px rgba(96,78,54,.09)",
    transition: "all .28s cubic-bezier(.4,0,.2,1)",
  };
}

export function catChip(c) {
  return {
    alignSelf: "flex-start",
    fontSize: "9px",
    fontWeight: 600,
    letterSpacing: ".05em",
    textTransform: "uppercase",
    padding: "3px 10px",
    borderRadius: "999px",
    background: "rgba(255,253,247,.72)",
    color: c,
  };
}

export function avatar(c, s = 16) {
  return {
    width: s + "px",
    height: s + "px",
    borderRadius: "50%",
    background: c,
    border: "2px solid rgba(255,253,247,.85)",
    flex: "none",
  };
}

export function dot(c) {
  return {
    width: "10px",
    height: "10px",
    borderRadius: "50%",
    background: c,
    flex: "none",
  };
}

// Heavy-glass hover popover
export function popover(right, up) {
  return {
    position: "absolute",
    ...(up ? { bottom: "calc(100% + 10px)" } : { top: "calc(100% + 10px)" }),
    ...(right ? { right: 0 } : { left: 0 }),
    width: "250px",
    zIndex: 40,
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    padding: "13px 14px",
    background: "rgba(255,253,247,.72)",
    backdropFilter: "blur(30px)",
    WebkitBackdropFilter: "blur(30px)",
    border: "1px solid rgba(255,255,255,.75)",
    borderRadius: "16px",
    boxShadow: "0 20px 44px rgba(45,45,45,.2)",
    animation: "popIn .18s cubic-bezier(.4,0,.2,1)",
  };
}

export const fieldStyle = {
  flex: 1,
  background:
    "linear-gradient(160deg, rgba(255,255,255,.55), rgba(255,255,255,.3))",
  border: "1px solid rgba(255,255,255,.65)",
  borderRadius: "13px",
  padding: "12px 15px",
  fontSize: "13.5px",
  outline: "none",
  minWidth: 0,
};

export const fieldRead = {
  background:
    "linear-gradient(160deg, rgba(255,255,255,.55), rgba(255,255,255,.3))",
  border: "1px solid rgba(255,255,255,.65)",
  borderRadius: "13px",
  padding: "12px 15px",
  fontSize: "13.5px",
  color: "#5c564b",
};

export const fieldLabel = {
  fontSize: "10px",
  fontWeight: 600,
  letterSpacing: ".07em",
  textTransform: "uppercase",
  color: "#a49c8c",
};

export const kicker = {
  fontSize: "10.5px",
  fontWeight: 600,
  letterSpacing: ".08em",
  textTransform: "uppercase",
  color: "#a49c8c",
};

export const agentBox = {
  borderRadius: "14px",
  padding: "12px 14px",
  background: "rgba(42,157,143,.13)",
  display: "flex",
  flexDirection: "column",
  gap: "4px",
};

export const toggleStyle = (on) => ({
  width: "44px",
  height: "24px",
  borderRadius: "999px",
  position: "relative",
  cursor: "pointer",
  flex: "none",
  transition: "all .2s",
  background: on
    ? "linear-gradient(160deg, #2A9D8F, #237c72)"
    : "linear-gradient(160deg, rgba(150,142,128,.3), rgba(150,142,128,.18))",
  border: "1px solid rgba(255,255,255,.55)",
});

export const knobStyle = (on) => ({
  position: "absolute",
  top: "2px",
  left: on ? "22px" : "2px",
  width: "18px",
  height: "18px",
  borderRadius: "50%",
  background: "#fff",
  boxShadow: "0 1px 4px rgba(0,0,0,.22)",
  transition: "left .2s cubic-bezier(.4,0,.2,1)",
});

export const prefCard = {
  borderRadius: "15px",
  padding: "13px 16px",
  display: "flex",
  alignItems: "center",
  gap: "12px",
  background: "rgba(255,253,247,.5)",
  border: "1px solid rgba(255,255,255,.6)",
};
