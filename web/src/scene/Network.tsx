import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Edges, Html } from "@react-three/drei";
import * as THREE from "three";
import { useBundle, useStore } from "../store";
import { DC_BOX, GRID_ROT_DEG, SITE_SCALE, toWorld, useCityGeo } from "./geom";
import { tempToUnit, thermal } from "../lib/format";

// ------------------------------------------------------------------ pipes

const flowVert = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;
const flowFrag = /* glsl */ `
  uniform float uTime; uniform float uSpeed; uniform float uLen; uniform float uDraw; uniform vec3 uColor; uniform float uGlow;
  varying vec2 vUv;
  void main() {
    if (vUv.x > uDraw) discard;
    float d = fract(vUv.x * uLen / 38.0 - uTime * uSpeed);
    float dash = smoothstep(0.0, 0.15, d) * (1.0 - smoothstep(0.45, 0.6, d));
    float rim = pow(abs(vUv.y - 0.5) * 2.0, 2.0);
    vec3 c = uColor * (0.75 + uGlow * dash) + uColor * rim * 0.2;
    gl_FragColor = vec4(c, 1.0);
  }
`;

function Pipe({ points, length }: { points: THREE.Vector3[]; length: number }) {
  const mat = useRef<THREE.ShaderMaterial>(null!);
  const geometry = useMemo(() => {
    const path = new THREE.CurvePath<THREE.Vector3>();
    for (let i = 1; i < points.length; i++) path.add(new THREE.LineCurve3(points[i - 1], points[i]));
    const site = useStore.getState().site;
    return new THREE.TubeGeometry(path, Math.max(8, points.length * 24), site === "chelsea" ? 5.5 : 3.6, 10, false);
  }, [points]);
  const uniforms = useMemo(() => ({
    uTime: { value: 0 }, uSpeed: { value: 1 }, uLen: { value: length }, uDraw: { value: 0 },
    uColor: { value: new THREE.Color("#22d3ee") }, uGlow: { value: 2.2 },
  }), [length]);
  const tmp = useMemo(() => new THREE.Color(), []);
  useFrame(({ clock }, dt) => {
    const s = useStore.getState();
    const f = s.frame;
    const target = s.step === "analyze" ? 0 : 1;
    const u = mat.current.uniforms;
    u.uDraw.value += (target - u.uDraw.value) * Math.min(1, dt * (target ? 0.9 : 4));
    u.uTime.value = clock.getElapsedTime();
    const flowFrac = f ? Math.min(1.6, f.loop.flow_m3h / 430) : 0.6;
    u.uSpeed.value = 0.25 + flowFrac * 1.4;
    u.uGlow.value = f && f.data_center.used_kw < 50 ? 0.4 : 2.4;
    thermal(tempToUnit(f ? f.loop.supply_temp_c : 25), tmp);
    (u.uColor.value as THREE.Color).lerp(tmp, Math.min(1, dt * 3));
  });
  return (
    <mesh geometry={geometry} raycast={() => null}>
      <shaderMaterial ref={mat} vertexShader={flowVert} fragmentShader={flowFrag} uniforms={uniforms} toneMapped={false} />
    </mesh>
  );
}

export function Pipes() {
  const bundle = useBundle();
  const site = useStore((s) => s.site);
  const placed = useCityGeo((g) => (g.site === site ? g.placed : null));
  const pipes = useMemo(() => (bundle?.pipes ?? []).map((p) => {
    const pts = p.points.map(([x, y]) => new THREE.Vector3(...toWorld(site, x, y, 5)));
    // finish each run at the real building it serves
    const end = placed?.[p.to];
    if (end) {
      const last = pts[pts.length - 1];
      if (Math.hypot(last.x - end.x, last.z - end.z) > 3) pts.push(new THREE.Vector3(end.x, 5, end.z));
    }
    const start = p.from !== "DC" ? placed?.[p.from] : null;
    if (start) {
      const first = pts[0];
      if (Math.hypot(first.x - start.x, first.z - start.z) > 3) pts.unshift(new THREE.Vector3(start.x, 5, start.z));
    }
    return { key: `${p.from}-${p.to}`, length: p.length_m * SITE_SCALE[site], points: pts };
  }), [bundle, site, placed]);
  return <group>{pipes.map((p) => <Pipe key={`${site}-${p.key}`} points={p.points} length={p.length} />)}</group>;
}

// ------------------------------------------------------------------ data center

export function DataCenter() {
  const site = useStore((s) => s.site);
  const bundle = useBundle();
  const box = DC_BOX[site];
  const core = useRef<THREE.MeshBasicMaterial>(null!);
  const halo = useRef<THREE.PointLight>(null!);
  const color = useMemo(() => new THREE.Color(), []);
  useFrame(({ clock }) => {
    const f = useStore.getState().frame;
    const cap = (bundle?.config.data_center.capacity_mw_th ?? 5) * 1000;
    const out = f ? f.data_center.offered_kw / cap : 0.7;
    const used = f ? f.data_center.used_kw / Math.max(f.data_center.offered_kw, 1) : 0.6;
    const pulse = 0.85 + 0.15 * Math.sin(clock.getElapsedTime() * (1.5 + out * 2));
    thermal(0.6 + 0.35 * used, color).multiplyScalar((0.2 + 1.6 * out) * pulse);
    core.current.color.copy(color);
    halo.current.intensity = 6e3 * out * pulse * (site === "chelsea" ? 1 : 0.4);
  });
  return (
    <group rotation={[0, -(GRID_ROT_DEG[site] * Math.PI) / 180, 0]}>
      <mesh position={[0, box.h / 2, 0]} castShadow receiveShadow>
        <boxGeometry args={[box.w, box.h, box.d]} />
        <meshStandardMaterial color="#3b4458" roughness={0.45} metalness={0.35} />
        <Edges color="#ff9a52" threshold={15} />
      </mesh>
      {/* heat-exchanger roof band: glows with the heat the network takes */}
      <mesh position={[0, box.h + 1.2, 0]} raycast={() => null}>
        <boxGeometry args={[box.w * 0.9, 2.4, box.d * 0.7]} />
        <meshBasicMaterial ref={core} toneMapped={false} />
      </mesh>
      <pointLight ref={halo} position={[0, box.h + 30, 0]} color="#ff9a52" distance={site === "chelsea" ? 700 : 300} decay={1.6} />
      <Html position={[0, box.h + 28, 0]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        <DcLabel name={bundle?.config.data_center.name ?? ""} />
      </Html>
      {site === "lansing" && bundle?.config.data_center.compute_follows_heat && <FlexRing radius={box.w * 0.95} />}
    </group>
  );
}

function DcLabel({ name }: { name: string }) {
  const f = useStore((s) => s.frame);
  return (
    <div className="text-center whitespace-nowrap -translate-y-3">
      <div className="label" style={{ color: "#ffb27a" }}>Data center</div>
      <div className="text-[12px] font-semibold">{name}</div>
      {f && <div className="num text-[11px] text-ink-2">{(f.data_center.offered_kw / 1000).toFixed(1)} MW heat · {f.data_center.freed_mw.toFixed(2)} MW freed</div>}
    </div>
  );
}

function FlexRing({ radius }: { radius: number }) {
  const ref = useRef<THREE.Mesh>(null!);
  const mat = useRef<THREE.MeshBasicMaterial>(null!);
  useFrame(({ clock }, dt) => {
    const f = useStore.getState().frame;
    const off = f?.active_scenarios.includes("bitcoin_price_crash");
    const shift = f?.data_center.shift_kw ?? 0;
    ref.current.rotation.z += dt * (shift > 0 ? 1.6 : shift < 0 ? -0.8 : 0.25);
    const c = off ? new THREE.Color("#3a4152") : shift > 0 ? new THREE.Color("#22d3ee").multiplyScalar(3) :
      shift < 0 ? new THREE.Color("#9085e9").multiplyScalar(2.5) : new THREE.Color("#22d3ee").multiplyScalar(0.9 + 0.3 * Math.sin(clock.getElapsedTime() * 2));
    mat.current.color.lerp(c, Math.min(1, dt * 4));
  });
  return (
    <mesh ref={ref} rotation={[-Math.PI / 2, 0, 0]} position={[0, 3, 0]} raycast={() => null}>
      <torusGeometry args={[radius, 1.6, 12, 128]} />
      <meshBasicMaterial ref={mat} toneMapped={false} color="#22d3ee" />
    </mesh>
  );
}

// ------------------------------------------------------------------ storage

export function Storage() {
  const site = useStore((s) => s.site);
  return site === "chelsea"
    ? <group rotation={[0, -(GRID_ROT_DEG.chelsea * Math.PI) / 180, 0]}><BoreholesAndTanks /></group>
    : <Pit />;
}

function BoreholesAndTanks() {
  const cols = useRef<THREE.InstancedMesh>(null!);
  const colMat = useRef<THREE.MeshBasicMaterial>(null!);
  const positions = useMemo(() => {
    const out: [number, number][] = [];
    for (let i = 0; i < 9; i++) for (let j = 0; j < 5; j++) out.push([-170 + i * 22, 120 + j * 20]);
    return out;
  }, []);
  useEffect(() => {
    const m = new THREE.Matrix4();
    positions.forEach(([x, z], i) => { m.makeTranslation(x, 38, z); cols.current.setMatrixAt(i, m); });
    cols.current.instanceMatrix.needsUpdate = true;
  }, [positions]);
  useFrame(({ clock }) => {
    const f = useStore.getState().frame;
    const bh = f?.storage.find((s) => s.type === "borehole");
    const t = clock.getElapsedTime();
    const flick = bh && bh.out_kw > 1 ? 0.75 + 0.25 * Math.sin(t * 9) : 1;
    thermal(0.25 + 0.6 * (bh?.soc_frac ?? 0.5), colMat.current.color).multiplyScalar((0.6 + 1.8 * (bh?.soc_frac ?? 0.5)) * flick);
  });
  return (
    <group>
      <instancedMesh ref={cols} args={[undefined, undefined, positions.length]} raycast={() => null}>
        <cylinderGeometry args={[2.2, 2.2, 76, 8]} />
        <meshBasicMaterial ref={colMat} toneMapped={false} transparent opacity={0.85} />
      </instancedMesh>
      <mesh position={[-82, 38, 160]} raycast={() => null}>
        <boxGeometry args={[220, 80, 110]} />
        <meshBasicMaterial color="#22d3ee" transparent opacity={0.07} depthWrite={false} />
        <Edges color="#0e7490" />
      </mesh>
      <group position={[200, 0, 95]}>
        {[-22, 22].map((dx) => (
          <mesh key={dx} position={[dx, 17, 0]} castShadow>
            <cylinderGeometry args={[14, 14, 34, 32]} />
            <meshStandardMaterial color="#0f1730" transparent opacity={0.45} metalness={0.6} roughness={0.3} />
          </mesh>
        ))}
        {[-22, 22].map((dx) => <TankFill key={dx} dx={dx} />)}
      </group>
      <Html position={[-82, 84, 160]} center style={{ pointerEvents: "none" }} zIndexRange={[5, 0]}>
        <div className="label whitespace-nowrap px-1.5 py-0.5 rounded" style={{ color: "#0e7490", background: "rgb(255 255 255 / 0.7)" }}>Borehole storage (shown above ground)</div>
      </Html>
    </group>
  );
}

function TankFill({ dx }: { dx: number }) {
  const ref = useRef<THREE.Mesh>(null!);
  const mat = useRef<THREE.MeshBasicMaterial>(null!);
  useFrame(({ clock }) => {
    const tk = useStore.getState().frame?.storage.find((s) => s.type === "hot_water_tank");
    const frac = Math.max(0.03, tk?.soc_frac ?? 0.6);
    ref.current.scale.y = frac;
    ref.current.position.y = (34 * frac) / 2;
    const pulse = tk && tk.out_kw > 1 ? 1 + 0.6 * Math.sin(clock.getElapsedTime() * 7) : 1;
    thermal(0.55 + 0.4 * frac, mat.current.color).multiplyScalar(1.6 * pulse);
  });
  return (
    <mesh ref={ref} position={[dx, 10, 0]} raycast={() => null}>
      <cylinderGeometry args={[12.5, 12.5, 34, 32]} />
      <meshBasicMaterial ref={mat} toneMapped={false} />
    </mesh>
  );
}

function Pit() {
  const water = useRef<THREE.Mesh>(null!);
  const mat = useRef<THREE.MeshBasicMaterial>(null!);
  const s = SITE_SCALE.lansing;
  const [x, , z] = toWorld("lansing", -40, 420);
  const w = 260 * s * 1.6, d = 200 * s * 1.6, depth = 30;
  useFrame(({ clock }) => {
    const pit = useStore.getState().frame?.storage[0];
    const frac = Math.max(0.03, pit?.soc_frac ?? 0.5);
    water.current.scale.y = frac;
    water.current.position.y = -depth + (depth * frac) / 2;
    const pulse = pit && pit.out_kw > 1 ? 1 + 0.4 * Math.sin(clock.getElapsedTime() * 5) : 1;
    thermal(0.45 + 0.45 * frac, mat.current.color).multiplyScalar(0.9 * pulse);
  });
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, -depth / 2, 0]} raycast={() => null}>
        <boxGeometry args={[w, depth, d]} />
        <meshBasicMaterial color="#67e8f9" transparent opacity={0.05} depthWrite={false} />
        <Edges color="#2aa7c4" />
      </mesh>
      <mesh ref={water} position={[0, -depth / 2, 0]} raycast={() => null}>
        <boxGeometry args={[w * 0.97, depth, d * 0.97]} />
        <meshBasicMaterial ref={mat} toneMapped={false} transparent opacity={0.55} />
      </mesh>
      <Html position={[0, 10, d / 2 + 10]} center style={{ pointerEvents: "none" }} zIndexRange={[5, 0]}>
        <div className="label whitespace-nowrap" style={{ color: "#67e8f9" }}>Pit storage · former coal yard</div>
      </Html>
    </group>
  );
}
