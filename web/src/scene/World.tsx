import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Line } from "@react-three/drei";
import * as THREE from "three";
import { useStore } from "../store";
import { SITE_SCALE } from "./geom";

const COLD = ["polar_vortex", "lake_effect_cold_snap"];

export function Ground() {
  const site = useStore((s) => s.site);
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.2, 0]} receiveShadow>
        <planeGeometry args={[9000, 9000]} />
        <meshStandardMaterial color="#070b16" roughness={0.95} metalness={0.05} transparent opacity={0.86} />
      </mesh>
      {site === "chelsea" ? <Streets /> : <Lansing />}
    </group>
  );
}

function Streets() {
  const lines = useMemo(() => {
    const out: [number, number, number][][] = [];
    for (let ax = -980; ax <= 980; ax += 280) out.push([[ax, 0.1, 420], [ax, 0.1, -1100]]);
    for (let sy = -360; sy <= 1080; sy += 80) out.push([[-1000, 0.1, -sy], [980, 0.1, -sy]]);
    return out;
  }, []);
  return (
    <group>
      {lines.map((pts, i) => <Line key={i} points={pts} color="#1a2440" lineWidth={1} transparent opacity={0.9} />)}
    </group>
  );
}

function Lansing() {
  const s = SITE_SCALE.lansing;
  const roads: [number, number][][] = [
    [[0, 0], [300, 0], [300, 3300]], [[300, 250], [3600, 250]], [[2400, 250], [2400, 1900], [3600, 1900]],
    [[1500, 250], [1500, 900]], [[2600, 1200], [3400, 1200]], [[900, 0], [900, -1400]],
  ];
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[-1000 * s - 900, 0.05, -800]}>
        <planeGeometry args={[1800, 6000]} />
        <meshStandardMaterial color="#0a1a33" roughness={0.15} metalness={0.6} />
      </mesh>
      {roads.map((r, i) => (
        <Line key={i} points={r.map(([x, y]) => [x * s, 0.1, -y * s] as [number, number, number])} color="#1d2a47" lineWidth={2} />
      ))}
    </group>
  );
}

export function Snow() {
  const ref = useRef<THREE.Points>(null!);
  const site = useStore((s) => s.site);
  const N = 5000;
  const positions = useMemo(() => {
    const a = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      a[i * 3] = (Math.random() - 0.5) * 2600;
      a[i * 3 + 1] = Math.random() * 700;
      a[i * 3 + 2] = (Math.random() - 0.5) * 2600 - 300;
    }
    return a;
  }, []);
  useFrame((_, dt) => {
    const f = useStore.getState().frame;
    const on = !!f && (f.active_scenarios.some((a) => COLD.includes(a)) || f.weather.t_out_c < -8);
    const mat = ref.current.material as THREE.PointsMaterial;
    mat.opacity += ((on ? 0.85 : 0) - mat.opacity) * Math.min(1, dt * 2);
    ref.current.visible = mat.opacity > 0.01;
    if (!ref.current.visible) return;
    const p = ref.current.geometry.attributes.position as THREE.BufferAttribute;
    for (let i = 0; i < N; i++) {
      let y = p.getY(i) - dt * (40 + (i % 7) * 6);
      if (y < 0) y += 700;
      p.setY(i, y);
      p.setX(i, p.getX(i) + Math.sin(y * 0.02 + i) * dt * 6);
    }
    p.needsUpdate = true;
  });
  return (
    <points ref={ref} key={site}>
      <bufferGeometry><bufferAttribute attach="attributes-position" args={[positions, 3]} /></bufferGeometry>
      <pointsMaterial color="#dbeafe" size={2.4} sizeAttenuation transparent opacity={0} depthWrite={false} />
    </points>
  );
}

/** Fog + light tint follow the weather: frost blue in cold snaps, warm haze in heat waves. */
export function Atmosphere() {
  const fog = useRef<THREE.Fog>(null!);
  const hemi = useRef<THREE.HemisphereLight>(null!);
  const site = useStore((s) => s.site);
  const cold = useMemo(() => new THREE.Color("#0c1a33"), []);
  const hot = useMemo(() => new THREE.Color("#2a140b"), []);
  const base = useMemo(() => new THREE.Color("#05070d"), []);
  useFrame(({ scene }, dt) => {
    const f = useStore.getState().frame;
    const isCold = !!f && (f.active_scenarios.some((a) => COLD.includes(a)) || f.weather.t_out_c < -8);
    const isHot = !!f && (f.active_scenarios.includes("heat_wave") || f.weather.t_out_c > 30);
    const target = isCold ? cold : isHot ? hot : base;
    fog.current.color.lerp(target, Math.min(1, dt * 1.5));
    (scene.background as THREE.Color | null)?.lerp(target, Math.min(1, dt * 1.5));
    hemi.current.color.lerp(isCold ? new THREE.Color("#9cc7ff") : isHot ? new THREE.Color("#ffb37a") : new THREE.Color("#8aa0d6"), Math.min(1, dt * 1.5));
  });
  const far = site === "chelsea" ? 3600 : 3000;
  return (
    <>
      <fog ref={fog} attach="fog" args={["#05070d", 900, far]} />
      <hemisphereLight ref={hemi} args={["#8aa0d6", "#05070d", 0.35]} />
    </>
  );
}
