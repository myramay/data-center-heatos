import { create } from "zustand";
import type { Building, SiteId } from "../types";

// World frame: x = metres east, z = -metres north of the site origin (the data center), y = up.
// Chelsea data is in Manhattan-grid metres (x crosstown, y uptown), rotated 29° from true north.
// Lansing spans ~6 km, so it is drawn at 0.28 with exaggerated heights.
export const SITE_SCALE: Record<SiteId, number> = { chelsea: 1, lansing: 0.28 };
export const HEIGHT_EXAG: Record<SiteId, number> = { chelsea: 1, lansing: 2.2 };
export const GRID_ROT_DEG: Record<SiteId, number> = { chelsea: 29, lansing: 0 };
export const ORIGIN: Record<SiteId, [number, number]> = { chelsea: [40.7411, -74.0032], lansing: [42.6025, -76.6338] };

/** Site data coordinates (grid metres) -> east/north metres. */
export function gridToEN(site: SiteId, x: number, y: number): [number, number] {
  const r = (GRID_ROT_DEG[site] * Math.PI) / 180;
  return [y * Math.sin(r) + x * Math.cos(r), y * Math.cos(r) - x * Math.sin(r)];
}

export function enToWorld(site: SiteId, e: number, n: number, h = 0): [number, number, number] {
  const s = SITE_SCALE[site];
  return [e * s, h, -n * s];
}

export function toWorld(site: SiteId, x: number, y: number, h = 0): [number, number, number] {
  const [e, n] = gridToEN(site, x, y);
  return enToWorld(site, e, n, h);
}

export function lonLatToEN(site: SiteId, lon: number, lat: number): [number, number] {
  const [lat0, lon0] = ORIGIN[site];
  return [(lon - lon0) * 111_320 * Math.cos((lat0 * Math.PI) / 180), (lat - lat0) * 111_320];
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
  chelsea: { pos: [-520, 560, 520], target: [40, 0, -210] },
  lansing: { pos: [-720, 640, 560], target: [380, 0, -360] },
};

// Where each candidate building is actually drawn (snapped to a real OSM footprint when one is close).
export interface Placed { x: number; z: number; h: number }
export const useCityGeo = create<{ site: SiteId | null; placed: Record<string, Placed>; set(site: SiteId, p: Record<string, Placed>): void }>((set) => ({
  site: null, placed: {}, set: (site, placed) => set({ site, placed }),
}));

/** World position + height of a candidate as drawn. */
export function placedOf(site: SiteId, b: Building): Placed {
  const g = useCityGeo.getState();
  if (g.site === site && g.placed[b.id]) return g.placed[b.id];
  const [x, , z] = toWorld(site, b.x_m, b.y_m);
  return { x, z, h: footprint(site, b).h };
}

// Mulberry32: deterministic pseudo-random.
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
