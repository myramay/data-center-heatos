import { useEffect, useMemo, useRef, useState } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { useBundle, useStore } from "../store";
import { DC_BOX, GRID_ROT_DEG, HEIGHT_EXAG, SITE_SCALE, footprint, gridToEN, enToWorld, useCityGeo } from "./geom";
import { OPTION_LABEL, mw, thermal } from "../lib/format";
import type { Building, SiteId } from "../types";

interface OsmBuilding { id: number; h: number; p: [number, number][]; n?: string }
interface Osm { buildings: OsmBuilding[] }

// Bright-scene status colours: only HeatOS candidate buildings carry colour.
export const STATUS = {
  network: "#ff7a1a", storage: "#f7b500", mixed: "#ff4d2e", backup: "#e5262b",
  candidate: "#7f97b8", plain: "#f2f4f7",
} as const;

const osmCache = new Map<SiteId, Promise<Osm | null>>();
function loadOsm(site: SiteId) {
  if (!osmCache.has(site)) osmCache.set(site, fetch(`/geo/${site}_osm.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  return osmCache.get(site)!;
}

function centroid(p: [number, number][]): [number, number] {
  let x = 0, y = 0;
  for (const [a, b] of p) { x += a; y += b; }
  return [x / p.length, y / p.length];
}

function extrude(site: SiteId, p: [number, number][], h: number): THREE.BufferGeometry | null {
  if (p.length < 3) return null;
  const s = SITE_SCALE[site];
  const shape = new THREE.Shape(p.map(([e, n]) => new THREE.Vector2(e * s, n * s)));
  const g = new THREE.ExtrudeGeometry(shape, { depth: h * s * HEIGHT_EXAG[site], bevelEnabled: false, curveSegments: 1 });
  g.rotateX(-Math.PI / 2);
  return g;
}

/** Matches each candidate to the nearest real footprint (within a radius) so the scene shows real buildings. */
function useMatched(site: SiteId, candidates: Building[], osm: Osm | null) {
  return useMemo(() => {
    const match = new Map<string, OsmBuilding>();
    if (!osm) return match;
    const maxD = site === "chelsea" ? 90 : 260;
    const cents = osm.buildings.map((b) => ({ b, c: centroid(b.p) }));
    const taken = new Set<number>();
    const dc = DC_BOX[site];
    const order = [...candidates].sort((a, b) => b.footprint_m2 - a.footprint_m2);
    for (const cand of order) {
      const [e, n] = gridToEN(site, cand.x_m, cand.y_m);
      let best: OsmBuilding | null = null, bestD = maxD;
      for (const { b, c } of cents) {
        if (taken.has(b.id)) continue;
        if (site === "chelsea" && Math.abs(c[0]) < dc.w / 2 && Math.abs(c[1]) < dc.w / 2 && Math.hypot(c[0], c[1]) < 80) continue;
        const d = Math.hypot(c[0] - e, c[1] - n);
        const area = Math.abs(b.p.reduce((a, [x1, y1], i) => { const [x2, y2] = b.p[(i + 1) % b.p.length]; return a + x1 * y2 - x2 * y1; }, 0)) / 2;
        if (area < 120) continue;                                    // skip sheds and kiosks
        if (d < bestD) { best = b; bestD = d; }
      }
      if (best) { match.set(cand.id, best); taken.add(best.id); }
    }
    return match;
  }, [site, candidates, osm]);
}

/** Plain context city from OpenStreetMap (one merged mesh) + coloured candidate buildings. */
export function City() {
  const bundle = useBundle();
  const site = useStore((s) => s.site);
  const [osm, setOsm] = useState<Osm | null>(null);
  useEffect(() => { setOsm(null); loadOsm(site).then(setOsm); }, [site]);
  const candidates = useMemo(() => bundle?.buildings ?? [], [bundle]);
  const matched = useMatched(site, candidates, osm);

  const context = useMemo(() => {
    if (!osm) return null;
    const used = new Set([...matched.values()].map((b) => b.id));
    const dc = DC_BOX[site];
    const r = (GRID_ROT_DEG[site] * Math.PI) / 180;
    const geos: THREE.BufferGeometry[] = [];
    for (const b of osm.buildings) {
      if (used.has(b.id)) continue;
      const [e, n] = centroid(b.p);
      // hide OSM buildings under the data center monolith
      const gx = e * Math.cos(r) - n * Math.sin(r), gy = e * Math.sin(r) + n * Math.cos(r);
      if (site === "chelsea" && Math.abs(gx) < dc.w / 2 && Math.abs(gy) < dc.d / 2 + 6) continue;
      if (site === "lansing" && Math.hypot(e, n) < 90) continue;
      const g = extrude(site, b.p, b.h);
      if (g) geos.push(g);
    }
    if (!geos.length) return null;
    const merged = mergeGeometries(geos, false);
    geos.forEach((g) => g.dispose());
    merged?.computeVertexNormals();
    return merged;
  }, [osm, matched, site]);

  useEffect(() => {
    const placed: Record<string, { x: number; z: number; h: number }> = {};
    for (const b of candidates) {
      const m = matched.get(b.id);
      if (m) {
        const [e, n] = centroid(m.p);
        const [x, , z] = enToWorld(site, e, n);
        placed[b.id] = { x, z, h: Math.max(m.h, 6) * SITE_SCALE[site] * HEIGHT_EXAG[site] };
      } else {
        const [e, n] = gridToEN(site, b.x_m, b.y_m);
        const [x, , z] = enToWorld(site, e, n);
        placed[b.id] = { x, z, h: footprint(site, b).h };
      }
    }
    useCityGeo.getState().set(site, placed);
  }, [candidates, matched, site]);

  return (
    <group>
      {context && (
        <mesh geometry={context} castShadow receiveShadow raycast={() => null}>
          <meshStandardMaterial color={STATUS.plain} roughness={0.85} metalness={0.02} />
        </mesh>
      )}
      {candidates.map((b) => <Candidate key={`${site}-${b.id}`} b={b} osm={matched.get(b.id) ?? null} />)}
      <HoverLabel buildings={candidates} />
    </group>
  );
}

function Candidate({ b, osm }: { b: Building; osm: OsmBuilding | null }) {
  const site = useStore((s) => s.site);
  const bundle = useBundle();
  const hover = useStore((s) => s.hover);
  const select = useStore((s) => s.select);
  const group = useRef<THREE.Group>(null!);
  const mat = useRef<THREE.MeshStandardMaterial>(null!);
  const riseStart = useRef(performance.now());
  const step = useStore((s) => s.step);
  useEffect(() => { if (step === "analyze") riseStart.current = performance.now(); }, [step]);
  useEffect(() => { riseStart.current = performance.now(); }, [site]);

  const geometry = useMemo(() => {
    if (osm) return extrude(site, osm.p, Math.max(osm.h, 6));
    const f = footprint(site, b);
    const [x, , z] = enToWorld(site, ...gridToEN(site, b.x_m, b.y_m));
    const g = new THREE.BoxGeometry(f.w, f.h, f.d);
    g.rotateY(-(GRID_ROT_DEG[site] * Math.PI) / 180);
    g.translate(x, f.h / 2, z);
    return g;
  }, [osm, site, b]);

  const connected = useMemo(() => !!bundle?.plan.items.find((i) => i.building_id === b.id && i.connect), [bundle, b.id]);
  const heatRank = useMemo(() => {
    const sorted = [...(bundle?.buildings ?? [])].sort((x, y) => x.annual_heat_mwh - y.annual_heat_mwh);
    const i = sorted.findIndex((x) => x.id === b.id);
    return sorted.length > 1 ? i / (sorted.length - 1) : 0.5;
  }, [bundle, b.id]);
  const tmp = useMemo(() => new THREE.Color(), []);
  const dist = Math.hypot(b.x_m, b.y_m);

  useFrame(({ clock }) => {
    const s = useStore.getState();
    const t = clock.getElapsedTime();
    const elapsed = (performance.now() - riseStart.current) / 1000 - (dist / 1500) * 1.2;
    const k = THREE.MathUtils.clamp(elapsed / 0.9, 0, 1);
    group.current.scale.y = Math.max(0.001, 1 - Math.pow(1 - k, 3));
    const selected = s.selected === b.id || s.hovered === b.id;
    let emissive = 0;
    if (s.step === "analyze") {
      thermal(0.05 + 0.95 * heatRank, tmp);
      emissive = 0.25;
    } else if (!connected) {
      tmp.set(STATUS.candidate);
    } else {
      const mode = s.frame?.buildings.find((x) => x.id === b.id)?.mode ?? "network";
      tmp.set(mode === "storage" ? STATUS.storage : mode === "backup" ? STATUS.backup : mode === "mixed" ? STATUS.mixed : STATUS.network);
      emissive = mode === "backup" || mode === "mixed" ? 0.35 + 0.45 * (0.5 + 0.5 * Math.sin(t * 6)) : 0.3;
    }
    mat.current.color.copy(tmp);
    mat.current.emissive.copy(tmp);
    mat.current.emissiveIntensity = emissive + (selected ? 0.35 : 0);
  });

  if (!geometry) return null;
  const onOver = (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); hover(b.id); };
  const onClick = (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); select(b.id); };
  return (
    <group ref={group}>
      <mesh geometry={geometry} castShadow receiveShadow onPointerOver={onOver} onPointerOut={() => hover(null)} onClick={onClick}>
        <meshStandardMaterial ref={mat} roughness={0.55} metalness={0.05} />
      </mesh>
    </group>
  );
}

function HoverLabel({ buildings }: { buildings: Building[] }) {
  const site = useStore((s) => s.site);
  const id = useStore((s) => s.hovered ?? s.selected);
  const frame = useStore((s) => s.frame);
  const step = useStore((s) => s.step);
  const placed = useCityGeo((g) => g.placed);
  const bundle = useBundle();
  const b = buildings.find((x) => x.id === id);
  if (!b || !placed[b.id]) return null;
  const p = placed[b.id];
  const fb = frame?.buildings.find((x) => x.id === b.id);
  const item = bundle?.plan.items.find((i) => i.building_id === b.id);
  return (
    <Html position={[p.x, p.h + 10 * SITE_SCALE[site] + 6, p.z]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
      <div className="glass px-3 py-2 whitespace-nowrap -translate-y-6" style={{ borderRadius: 10 }}>
        <div className="text-[12px] font-semibold text-ink">{b.name}</div>
        <div className="text-[10.5px] text-ink-2 mt-0.5">
          {b.use_type.replace("_", " ")} · {b.heating_system.replace("_", " ")} · {Math.round(b.annual_heat_mwh).toLocaleString()} MWh/yr
        </div>
        {step !== "analyze" && (
          <div className="text-[10.5px] mt-1 num" style={{ color: item?.connect ? "#ffb27a" : "#8a94ab" }}>
            {item?.connect ? `${OPTION_LABEL[item.option] ?? ""} · ${fb ? `${fb.mode} · ${mw(fb.delivered_kw)}` : "connected"}` : "not connected"}
          </div>
        )}
      </div>
    </Html>
  );
}
