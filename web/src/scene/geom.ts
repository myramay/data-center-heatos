import type { Building, SiteId } from "../types";

// World units: Chelsea 1 m = 1 unit; Lansing spans ~5 km, so it is drawn at 0.28 with taller buildings.
export const SITE_SCALE: Record<SiteId, number> = { chelsea: 1, lansing: 0.28 };
export const HEIGHT_EXAG: Record<SiteId, number> = { chelsea: 1, lansing: 2.2 };

export function toWorld(site: SiteId, x: number, y: number, h = 0): [number, number, number] {
  const s = SITE_SCALE[site];
  return [x * s, h, -y * s];
}

export function footprint(site: SiteId, b: Building): { w: number; d: number; h: number } {
  const s = SITE_SCALE[site];
  const side = Math.min(Math.sqrt(b.footprint_m2), 110) * s;
  const h = Math.max(b.height_m * s * HEIGHT_EXAG[site], 3);
  if (b.use_type === "greenhouse") return { w: side * 1.25, d: side * 0.8, h: Math.max(h, 6) };
  if (b.use_type === "home") return { w: side * 1.6, d: side * 0.6, h: Math.max(h, 4) };
  return { w: side, d: side, h };
}

export const DC_BOX: Record<SiteId, { w: number; d: number; h: number }> = {
  chelsea: { w: 276, d: 76, h: 72 },
  lansing: { w: 120 * 0.28 * 2.2, d: 120 * 0.28 * 1.6, h: 22 * 0.28 * 2.2 },
};

export const CAMERA_HOME: Record<SiteId, { pos: [number, number, number]; target: [number, number, number] }> = {
  chelsea: { pos: [-620, 470, 760], target: [-60, 0, -230] },
  lansing: { pos: [-760, 620, 520], target: [380, 0, -360] },
};

// Mulberry32: deterministic pseudo-random for procedural context buildings.
export function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
