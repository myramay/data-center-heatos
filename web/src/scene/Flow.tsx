import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import { useBundle, useStore } from "../store";
import { SITE_SCALE, enToWorld, toWorld, useCityGeo, GRID_ROT_DEG } from "./geom";
import { MAT } from "./Model";

const HEAT = new THREE.Color("#ffb04a");
const HOT = new THREE.Color("#ff6a1a");
const COLD = new THREE.Color("#ff3b3b");
const tmpM = new THREE.Matrix4();
const tmpV = new THREE.Vector3();
const tmpS = new THREE.Vector3();
const tmpQ = new THREE.Quaternion();

/** Glowing heat packets that travel along a pipe from the data center outward. */
function Packets({ points, length, target }: { points: THREE.Vector3[]; length: number; target: string | null }) {
  const site = useStore((s) => s.site);
  const ref = useRef<THREE.InstancedMesh>(null!);
  const mat = useRef<THREE.MeshBasicMaterial>(null!);
  const path = useMemo(() => {
    const p = new THREE.CurvePath<THREE.Vector3>();
    for (let i = 1; i < points.length; i++) p.add(new THREE.LineCurve3(points[i - 1], points[i]));
    return p;
  }, [points]);
  const n = Math.max(3, Math.min(48, Math.round(length / (site === "chelsea" ? 42 : 30))));
  const r = site === "chelsea" ? 6.5 : 4.2;
  const phase = useRef(Math.random());
  useFrame((_, dt) => {
    const f = useStore.getState().frame;
    const step = useStore.getState().step;
    const visible = step !== "analyze";
    ref.current.visible = visible;
    if (!visible) return;
    const flowFrac = f ? Math.min(1.8, f.loop.flow_m3h / 430) : 0.6;
    const dcOn = !f || f.data_center.used_kw > 50;
    const tb = target && f ? f.buildings.find((b) => b.id === target) : null;
    const share = tb ? (tb.delivered_kw + tb.unmet_kw > 0 ? tb.delivered_kw / (tb.delivered_kw + tb.unmet_kw) : 0) : 1;
    // metres per second along the pipe (scaled for readability), proportional to loop flow
    phase.current += (dt * (40 + 120 * flowFrac) * (dcOn ? 1 : 0.05)) / Math.max(length, 1);
    for (let i = 0; i < n; i++) {
      const u = (i / n + phase.current) % 1;
      path.getPointAt(u, tmpV);
      const pulse = 0.75 + 0.25 * Math.sin((u + phase.current) * Math.PI * 6);
      const scale = r * (dcOn ? 0.55 + 0.6 * share : 0.35) * pulse;
      tmpM.compose(tmpV, tmpQ, tmpS.set(scale, scale, scale));
      ref.current.setMatrixAt(i, tmpM);
    }
    ref.current.instanceMatrix.needsUpdate = true;
    const c = share < 0.2 && tb ? COLD : HEAT.clone().lerp(HOT, Math.min(1, flowFrac / 1.6));
    mat.current.color.copy(c).multiplyScalar(dcOn ? 2.2 : 0.6);
  });
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, n]} raycast={() => null} frustumCulled={false}>
      <sphereGeometry args={[1, 10, 8]} />
      <meshBasicMaterial ref={mat} toneMapped={false} transparent opacity={0.95} />
    </instancedMesh>
  );
}

/** Heat packets on every pipe of the plan, ending at the real building each run serves. */
export function HeatFlow() {
  const bundle = useBundle();
  const site = useStore((s) => s.site);
  const placed = useCityGeo((g) => (g.site === site ? g.placed : null));
  const runs = useMemo(() => (bundle?.pipes ?? []).map((p) => {
    const pts = p.points.map(([x, y]) => new THREE.Vector3(...toWorld(site, x, y, 12)));
    const end = placed?.[p.to];
    if (end) {
      const last = pts[pts.length - 1];
      if (Math.hypot(last.x - end.x, last.z - end.z) > 3) pts.push(new THREE.Vector3(end.x, 12, end.z));
    }
    return { key: `${p.from}-${p.to}`, pts, length: p.length_m * SITE_SCALE[site], target: p.to };
  }), [bundle, site, placed]);
  return <group>{runs.map((r) => <Packets key={`${site}-${r.key}`} points={r.pts} length={r.length} target={r.target} />)}</group>;
}

/** Ring pulses at connected buildings: amber while network heat arrives, red while on backup. */
export function ArrivalPulses() {
  const bundle = useBundle();
  const site = useStore((s) => s.site);
  const placed = useCityGeo((g) => (g.site === site ? g.placed : null));
  const ids = useMemo(() => bundle?.plan.items.filter((i) => i.connect).map((i) => i.building_id) ?? [], [bundle]);
  return <group>{ids.map((id) => placed?.[id] ? <Pulse key={id} id={id} x={placed[id].x} z={placed[id].z} /> : null)}</group>;
}

function Pulse({ id, x, z }: { id: string; x: number; z: number }) {
  const site = useStore((s) => s.site);
  const ref = useRef<THREE.Mesh>(null!);
  const mat = useRef<THREE.MeshBasicMaterial>(null!);
  const base = site === "chelsea" ? 26 : 12;
  const off = useMemo(() => Math.random(), []);
  useFrame(({ clock }) => {
    const s = useStore.getState();
    const fb = s.frame?.buildings.find((b) => b.id === id);
    const on = s.step !== "analyze" && !!fb && fb.delivered_kw + fb.unmet_kw > 1;
    ref.current.visible = on;
    if (!on) return;
    const k = (clock.getElapsedTime() * 0.7 + off) % 1;
    ref.current.scale.setScalar(base * (0.6 + 1.6 * k));
    const backup = fb!.backup_on;
    mat.current.color.set(backup ? "#ff3b3b" : "#ffb04a").multiplyScalar(1.8);
    mat.current.opacity = (1 - k) * 0.85;
  });
  return (
    <mesh ref={ref} position={[x, 2.5, z]} rotation={[-Math.PI / 2, 0, 0]} raycast={() => null}>
      <ringGeometry args={[0.82, 1, 48]} />
      <meshBasicMaterial ref={mat} toneMapped={false} transparent depthWrite={false} />
    </mesh>
  );
}

/** Packets between the data center and storage: toward storage when charging, back when discharging. */
export function StorageFlow() {
  const site = useStore((s) => s.site);
  const ref = useRef<THREE.InstancedMesh>(null!);
  const mat = useRef<THREE.MeshBasicMaterial>(null!);
  const path = useMemo(() => {
    const r = -(GRID_ROT_DEG[site] * Math.PI) / 180;
    const rot = (x: number, z: number) => new THREE.Vector3(x * Math.cos(r) + z * Math.sin(r), 10, -x * Math.sin(r) + z * Math.cos(r));
    const pts = site === "chelsea"
      ? [rot(0, 45), rot(-40, 110), rot(-82, 150)]
      : [new THREE.Vector3(0, 6, 0), new THREE.Vector3(...enToWorld("lansing", -40, 420, 6))];
    return new THREE.CatmullRomCurve3(pts);
  }, [site]);
  const n = 14;
  const phase = useRef(0);
  useFrame((_, dt) => {
    const f = useStore.getState().frame;
    const ins = f?.storage.reduce((a, s) => a + s.in_kw, 0) ?? 0;
    const outs = f?.storage.reduce((a, s) => a + s.out_kw, 0) ?? 0;
    const active = useStore.getState().step !== "analyze" && (ins > 20 || outs > 20);
    ref.current.visible = active;
    if (!active) return;
    const dir = ins >= outs ? 1 : -1;
    phase.current = (phase.current + dir * dt * 0.35 + 1) % 1;
    const sz = site === "chelsea" ? 5 : 3.2;
    for (let i = 0; i < n; i++) {
      path.getPointAt((i / n + phase.current) % 1, tmpV);
      tmpM.compose(tmpV, tmpQ, tmpS.set(sz, sz, sz));
      ref.current.setMatrixAt(i, tmpM);
    }
    ref.current.instanceMatrix.needsUpdate = true;
    mat.current.color.set(dir > 0 ? "#2dd4bf" : "#ffb04a").multiplyScalar(2);
  });
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, n]} raycast={() => null} frustumCulled={false}>
      <sphereGeometry args={[1, 10, 8]} />
      <meshBasicMaterial ref={mat} toneMapped={false} />
    </instancedMesh>
  );
}

// ------------------------------------------------------------------ team plan + infrastructure layers

const INFRA_COLOR: Record<string, string> = {
  public_housing: "#3987e5", CHP: "#d95926", steam_network: "#c3b8ff", hot_water_network: "#199e70",
  industrial_waste_heat: "#ff7a1a", pool: "#22d3ee", central_utility_plant: "#c98500",
};

export function TeamLayer() {
  const bundle = useBundle();
  const site = useStore((s) => s.site);
  const show = useStore((s) => s.showTeam);
  const lines = useMemo(() => (bundle?.team?.pipes ?? []).map((p) => {
    const pts = p.points.map(([e, n]) => new THREE.Vector3(...enToWorld(site, e, n, 18)));
    const path = new THREE.CurvePath<THREE.Vector3>();
    for (let i = 1; i < pts.length; i++) path.add(new THREE.LineCurve3(pts[i - 1], pts[i]));
    const kw = p.peak_kw_th ?? 500;
    return { geo: new THREE.TubeGeometry(path, Math.max(4, pts.length * 6), (site === "chelsea" ? 2.2 : 1.6) + Math.min(4, kw / 2500), 6, false), kw };
  }), [bundle, site]);
  if (!show || !lines.length) return null;
  return (
    <group>
      {lines.map((l, i) => (
        <mesh key={i} geometry={l.geo} raycast={() => null}>
          <meshStandardMaterial color="#7c3aed" emissive="#7c3aed" emissiveIntensity={0.6} transparent opacity={0.85} />
        </mesh>
      ))}
    </group>
  );
}

export function InfraLayer() {
  const bundle = useBundle();
  const site = useStore((s) => s.site);
  const show = useStore((s) => s.showInfra);
  const items = bundle?.team?.infrastructure ?? [];
  if (!show || !items.length) return null;
  const s = SITE_SCALE[site];
  return (
    <group>
      {items.map((a, i) => {
        const [x, , z] = enToWorld(site, a.east, a.north);
        const c = INFRA_COLOR[a.type] ?? "#8b95a3";
        const h = (90 + (i % 4) * 55) * Math.max(s, 0.5);       // staggered so neighbouring labels don't collide
        return (
          <group key={a.id} position={[x, 0, z]}>
            <mesh position={[0, h / 2, 0]} material={MAT.chrome}><cylinderGeometry args={[1.6, 1.6, h, 8]} /></mesh>
            <mesh position={[0, h + 6, 0]}>
              <sphereGeometry args={[7, 16, 12]} />
              <meshStandardMaterial color={c} emissive={c} emissiveIntensity={0.5} metalness={0.3} roughness={0.35} />
            </mesh>
            <Html position={[0, h + 22, 0]} center zIndexRange={[6, 0]} style={{ pointerEvents: "none" }}>
              <div className="whitespace-nowrap px-1.5 py-0.5 rounded text-[9.5px] font-semibold" style={{ background: "rgb(255 255 255 / 0.82)", color: "#1f2937", borderLeft: `3px solid ${c}` }}>
                {a.name.length > 30 ? a.name.slice(0, 29) + "…" : a.name}
              </div>
            </Html>
          </group>
        );
      })}
    </group>
  );
}
