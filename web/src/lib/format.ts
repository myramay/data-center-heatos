import * as THREE from "three";

export const usd = (v: number, digits = 0) => {
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(a >= 1e7 ? 1 : 2)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}k`;
  return `${sign}$${a.toFixed(digits)}`;
};
export const num = (v: number, digits = 0) => v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
export const mw = (kw: number) => `${(kw / 1000).toFixed(2)} MW`;
export const pct = (p: number | null | undefined, digits = 0) => (p == null ? "–" : `${(p * 100).toFixed(digits)}%`);
export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export const OPTION_LABEL: Record<string, string> = {
  direct_link: "Direct link", loop_hp: "Loop + heat pump", steam_hp: "Steam heat pump",
  direct_use: "Direct use", booster: "Loop + booster", central_hp: "Hot-water network", not_connected: "Not connected",
};

// Thermal ramp: deep indigo -> cyan -> amber -> red-hot (for heat in the 3D scene).
const STOPS: [number, THREE.Color][] = [
  [0, new THREE.Color("#3b2a8f")], [0.35, new THREE.Color("#22d3ee")],
  [0.7, new THREE.Color("#f5a524")], [1, new THREE.Color("#ff3b3b")],
];
export function thermal(t: number, out = new THREE.Color()): THREE.Color {
  const x = Math.min(1, Math.max(0, t));
  for (let i = 1; i < STOPS.length; i++) {
    if (x <= STOPS[i][0]) {
      const [a, ca] = STOPS[i - 1];
      const [b, cb] = STOPS[i];
      return out.copy(ca).lerp(cb, (x - a) / (b - a));
    }
  }
  return out.copy(STOPS[STOPS.length - 1][1]);
}
export const tempToUnit = (c: number) => (c - 10) / 60;      // 10 C -> 0, 70 C -> 1

// Building status colours in the scene (spec: warm, amber on storage, red pulse on backup, grey off).
export const MODE_COLOR = {
  network: "#ff8a3d",
  storage: "#ffd23f",
  mixed: "#ff5a3c",
  backup: "#ff2d2d",
  off: "#2a3348",
  candidate: "#475a7d",
} as const;

// Categorical party colours (validated, dataviz reference dark order); externals neutral.
const PARTY_SLOTS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"];
export function partyColor(id: string, parties: { id: string }[]): string {
  const i = parties.findIndex((p) => p.id === id);
  return i >= 0 ? PARTY_SLOTS[i % PARTY_SLOTS.length] : "#5b6478";
}
