import { useEffect, useMemo, useRef } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import { useBundle, useStore } from "../store";
import { footprint, rng, toWorld, SITE_SCALE } from "./geom";
import { MODE_COLOR, OPTION_LABEL, mw, thermal } from "../lib/format";
import type { Building } from "../types";

const tmpM = new THREE.Matrix4();
const tmpQ = new THREE.Quaternion();
const tmpS = new THREE.Vector3();
const tmpP = new THREE.Vector3();
const tmpC = new THREE.Color();

/** Candidate buildings: instanced bodies + instanced glowing roof caps (status colour, bloom). */
export function Buildings() {
  const bundle = useBundle();
  const site = useStore((s) => s.site);
  const step = useStore((s) => s.step);
  const hover = useStore((s) => s.hover);
  const select = useStore((s) => s.select);
  const hovered = useStore((s) => s.hovered);
  const selected = useStore((s) => s.selected);
  const body = useRef<THREE.InstancedMesh>(null!);
  const cap = useRef<THREE.InstancedMesh>(null!);
  const rise = useRef(0);
  const riseStart = useRef(0);

  const buildings = bundle?.buildings ?? [];
  const connected = useMemo(() => new Set(bundle?.plan.items.filter((i) => i.connect).map((i) => i.building_id)), [bundle]);
  const heatRank = useMemo(() => {
    const sorted = [...buildings].sort((a, b) => a.annual_heat_mwh - b.annual_heat_mwh);
    return new Map(sorted.map((b, i) => [b.id, sorted.length > 1 ? i / (sorted.length - 1) : 0.5]));
  }, [buildings]);
  const geo = useMemo(() => buildings.map((b) => ({ b, f: footprint(site, b), p: toWorld(site, b.x_m, b.y_m) })), [buildings, site]);
  const dist = useMemo(() => geo.map(({ p }) => Math.hypot(p[0], p[2])), [geo]);
  const maxDist = Math.max(1, ...dist);

  useEffect(() => { rise.current = 0; riseStart.current = performance.now(); }, [site, bundle]);
  useEffect(() => { if (step === "analyze") { rise.current = 0; riseStart.current = performance.now(); } }, [step]);

  useFrame(({ clock }) => {
    if (!body.current || !geo.length) return;
    const t = clock.getElapsedTime();
    const elapsed = (performance.now() - riseStart.current) / 1000;
    rise.current = Math.min(1, elapsed / 2.6);
    const frame = useStore.getState().frame;
    const byId = new Map(frame?.buildings.map((fb) => [fb.id, fb]) ?? []);
    const showStatus = step !== "analyze";
    geo.forEach(({ b, f, p }, i) => {
      const local = THREE.MathUtils.clamp((elapsed - (dist[i] / maxDist) * 1.4) / 0.9, 0, 1);
      const e = 1 - Math.pow(1 - local, 3);
      const h = Math.max(f.h * e, 0.01);
      tmpM.compose(tmpP.set(p[0], h / 2, p[2]), tmpQ.identity(), tmpS.set(f.w, h, f.d));
      body.current.setMatrixAt(i, tmpM);
      tmpM.compose(tmpP.set(p[0], h + 0.6, p[2]), tmpQ.identity(), tmpS.set(f.w * 1.001, 1.2, f.d * 1.001));
      cap.current.setMatrixAt(i, tmpM);

      let intensity = 1.0;
      if (!showStatus) {
        thermal(0.05 + 0.95 * (heatRank.get(b.id) ?? 0.5), tmpC);
        intensity = 1.2;
      } else if (!connected.has(b.id)) {
        tmpC.set("#3d4a6b");
        intensity = 0.9;
      } else {
        const fb = byId.get(b.id);
        const mode = fb?.mode ?? "network";
        tmpC.set(MODE_COLOR[mode === "off" ? "network" : mode]);
        intensity = mode === "backup" || mode === "mixed" ? 1.2 + 1.4 * (0.5 + 0.5 * Math.sin(t * 6)) : 1.7;
        if (b.use_type === "greenhouse") intensity *= 1.3;
      }
      if (b.id === hovered || b.id === selected) intensity *= 1.25;
      cap.current.setColorAt(i, tmpC.multiplyScalar(intensity));
      const bodyTone = showStatus && connected.has(b.id) ? 0.3 : 0.2;
      body.current.setColorAt(i, tmpC.set(showStatus && connected.has(b.id) ? MODE_COLOR.network : "#8fa3c7").multiplyScalar(bodyTone));
    });
    body.current.instanceMatrix.needsUpdate = true;
    cap.current.instanceMatrix.needsUpdate = true;
    if (body.current.instanceColor) body.current.instanceColor.needsUpdate = true;
    if (cap.current.instanceColor) cap.current.instanceColor.needsUpdate = true;
  });

  const onMove = (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); if (e.instanceId != null) hover(buildings[e.instanceId]?.id ?? null); };
  const onClick = (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); if (e.instanceId != null) select(buildings[e.instanceId]?.id ?? null); };

  if (!geo.length) return null;
  return (
    <group>
      <instancedMesh key={`b-${site}-${geo.length}`} ref={body} args={[undefined, undefined, geo.length]} castShadow receiveShadow
                     onPointerMove={onMove} onPointerOut={() => hover(null)} onClick={onClick}>
        <boxGeometry />
        <meshStandardMaterial color="#ffffff" roughness={0.55} metalness={0.25} />
      </instancedMesh>
      <instancedMesh key={`c-${site}-${geo.length}`} ref={cap} args={[undefined, undefined, geo.length]}>
        <boxGeometry />
        <meshBasicMaterial color="#ffffff" toneMapped={false} />
      </instancedMesh>
      <HoverLabel buildings={buildings} connected={connected} />
    </group>
  );
}

function HoverLabel({ buildings, connected }: { buildings: Building[]; connected: Set<string> }) {
  const site = useStore((s) => s.site);
  const id = useStore((s) => s.hovered ?? s.selected);
  const frame = useStore((s) => s.frame);
  const step = useStore((s) => s.step);
  const bundle = useBundle();
  const b = buildings.find((x) => x.id === id);
  if (!b) return null;
  const f = footprint(site, b);
  const p = toWorld(site, b.x_m, b.y_m, f.h + 14 * SITE_SCALE[site] + 6);
  const fb = frame?.buildings.find((x) => x.id === b.id);
  const item = bundle?.plan.items.find((i) => i.building_id === b.id);
  return (
    <Html position={p} center distanceFactor={undefined} zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
      <div className="glass px-3 py-2 whitespace-nowrap -translate-y-6" style={{ borderRadius: 10 }}>
        <div className="text-[12px] font-semibold text-ink">{b.name}</div>
        <div className="text-[10.5px] text-ink-2 mt-0.5">
          {b.use_type.replace("_", " ")} · {b.heating_system.replace("_", " ")} · {Math.round(b.annual_heat_mwh).toLocaleString()} MWh/yr
        </div>
        {step !== "analyze" && (
          <div className="text-[10.5px] mt-1 num" style={{ color: connected.has(b.id) ? "#ffb27a" : "#8a94ab" }}>
            {connected.has(b.id)
              ? `${OPTION_LABEL[item?.option ?? ""] ?? ""} · ${fb ? `${fb.mode} · ${mw(fb.delivered_kw)}` : "connected"}`
              : "not connected"}
          </div>
        )}
      </div>
    </Html>
  );
}

/** Procedural surrounding blocks (Chelsea) for context; dim, instanced, never interactive. */
export function ContextCity() {
  const site = useStore((s) => s.site);
  const bundle = useBundle();
  const ref = useRef<THREE.InstancedMesh>(null!);
  const boxes = useMemo(() => {
    if (site !== "chelsea" || !bundle) return [];
    const r = rng(42);
    const occupied = bundle.buildings.map((b) => [b.x_m, b.y_m, Math.sqrt(b.footprint_m2) / 2 + 30] as const);
    const out: { x: number; y: number; w: number; d: number; h: number }[] = [];
    for (let ax = -980; ax <= 700; ax += 280) {
      for (let sy = -360; sy <= 1040; sy += 80) {
        const n = 2 + Math.floor(r() * 3);
        for (let k = 0; k < n; k++) {
          const x = ax + 20 + r() * 240;
          const y = sy + 10 + r() * 60;
          if (Math.abs(x) < 150 && Math.abs(y) < 50) continue;                       // data center block
          if (occupied.some(([bx, by, rad]) => Math.hypot(bx - x, by - y) < rad)) continue;
          out.push({ x, y, w: 25 + r() * 40, d: 22 + r() * 30, h: 12 + Math.pow(r(), 2.2) * 90 });
        }
      }
    }
    return out;
  }, [site, bundle]);

  useEffect(() => {
    if (!ref.current) return;
    boxes.forEach((b, i) => {
      tmpM.compose(tmpP.set(b.x, b.h / 2, -b.y), tmpQ.identity(), tmpS.set(b.w, b.h, b.d));
      ref.current.setMatrixAt(i, tmpM);
    });
    ref.current.instanceMatrix.needsUpdate = true;
  }, [boxes]);

  if (!boxes.length) return null;
  return (
    <instancedMesh key={boxes.length} ref={ref} args={[undefined, undefined, boxes.length]} receiveShadow castShadow raycast={() => null}>
      <boxGeometry />
      <meshStandardMaterial color="#1b2440" roughness={0.85} metalness={0.15} />
    </instancedMesh>
  );
}
