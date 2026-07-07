// Dark-surface palette (validated reference set; fixed hazard→slot assignment
// so a hazard's color never depends on what else is on the map).
export const HAZARD_COLORS: Record<string, string> = {
  coastal_flooding: "#3987e5", // blue
  high_waves: "#199e70",       // aqua
  rip_current: "#c98500",      // yellow
  algal_bloom: "#008300",      // green
  storm_surge: "#9085e9",      // violet
  tsunami: "#e66767",          // red
  erosion: "#d55181",          // magenta
  oil_spill: "#d95926",        // orange
  other: "#898781",            // folds into muted, never a ninth hue
};

export const HAZARD_LABELS: Record<string, string> = {
  coastal_flooding: "Coastal flooding",
  storm_surge: "Storm surge",
  high_waves: "High waves",
  tsunami: "Tsunami signs",
  rip_current: "Rip current",
  oil_spill: "Oil spill",
  algal_bloom: "Algal bloom",
  erosion: "Erosion",
  other: "Other",
};

// Status palette — reserved, never reused for series; always shown with a label.
export const STATUS_COLORS: Record<string, string> = {
  unverified: "#898781",
  corroborated: "#fab219",
  verified: "#0ca30c",
  rejected: "#d03b3b",
};

export const STATUS_LABELS: Record<string, string> = {
  unverified: "Unverified",
  corroborated: "Corroborated",
  verified: "Verified",
  rejected: "Rejected",
};

export const INK = {
  primary: "#ffffff",
  secondary: "#c3c2b7",
  muted: "#898781",
  grid: "#2c2c2a",
  surface: "#1a1a19",
  accent: "#3987e5",
  critical: "#d03b3b",
};
