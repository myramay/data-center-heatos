import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { Bloom, ChromaticAberration, EffectComposer, Noise, Vignette } from "@react-three/postprocessing";
import { BlendFunction } from "postprocessing";
import * as THREE from "three";
import type { OrbitControls as OrbitImpl } from "three-stdlib";
import { useBundle, useStore } from "../store";
import { Buildings, ContextCity } from "./Buildings";
import { DataCenter, Pipes, Storage } from "./Network";
import { Atmosphere, Ground, Snow } from "./World";
import { CAMERA_HOME, footprint, toWorld } from "./geom";

function CameraRig() {
  const controls = useRef<OrbitImpl>(null!);
  const { camera } = useThree();
  const site = useStore((s) => s.site);
  const selected = useStore((s) => s.selected);
  const cinematic = useStore((s) => s.cinematic);
  const bundle = useBundle();
  const lastInput = useRef(performance.now());
  const fly = useRef<{ pos: THREE.Vector3; target: THREE.Vector3 } | null>(null);
  const cine = useRef<{ start: number; pos: THREE.CatmullRomCurve3; look: THREE.CatmullRomCurve3 } | null>(null);

  useEffect(() => {
    const home = CAMERA_HOME[site];
    fly.current = { pos: new THREE.Vector3(...home.pos), target: new THREE.Vector3(...home.target) };
  }, [site]);

  useEffect(() => {
    const b = bundle?.buildings.find((x) => x.id === selected);
    if (!b) {
      const home = CAMERA_HOME[site];
      fly.current = { pos: new THREE.Vector3(...home.pos), target: new THREE.Vector3(...home.target) };
      return;
    }
    const [x, , z] = toWorld(site, b.x_m, b.y_m);
    const h = footprint(site, b).h;
    const target = new THREE.Vector3(x, h * 0.5, z);
    const dir = new THREE.Vector3().subVectors(camera.position, controls.current?.target ?? target).setY(0).normalize();
    const dist = site === "chelsea" ? 380 : 260;
    fly.current = { pos: target.clone().add(dir.multiplyScalar(dist)).setY(h + dist * 0.55), target };
  }, [selected, bundle, site, camera]);

  useEffect(() => {
    if (!cinematic) { cine.current = null; return; }
    const S = site === "chelsea" ? 1 : 0.9;
    const pts = (site === "chelsea"
      ? [[-1300, 900, 1300], [-500, 260, 500], [-80, 180, 140], [420, 240, -60], [380, 420, -700], [-500, 520, -900], [-1100, 700, 200]]
      : [[-700, 800, 900], [-60, 160, 260], [140, 120, 60], [600, 260, -260], [950, 420, -560], [300, 700, -900], [-600, 760, 300]]
    ).map(([x, y, z]) => new THREE.Vector3(x * S, y, z * S));
    const looks = (site === "chelsea"
      ? [[0, 0, -200], [-200, 40, -120], [0, 40, 0], [-250, 30, -200], [-300, 20, -600], [0, 0, -300], [0, 0, -260]]
      : [[300, 0, -300], [0, 0, 0], [80, 0, 60], [600, 0, -350], [850, 0, -450], [400, 0, -400], [380, 0, -360]]
    ).map(([x, y, z]) => new THREE.Vector3(x, y, z));
    cine.current = { start: performance.now(), pos: new THREE.CatmullRomCurve3(pts), look: new THREE.CatmullRomCurve3(looks) };
  }, [cinematic, site]);

  useFrame((_, dt) => {
    const c = controls.current;
    if (!c) return;
    if (cine.current) {
      const t = (performance.now() - cine.current.start) / 20000;
      if (t >= 1) { useStore.getState().setCinematic(false); cine.current = null; return; }
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      camera.position.copy(cine.current.pos.getPoint(e));
      c.target.copy(cine.current.look.getPoint(e));
      c.update();
      return;
    }
    if (fly.current) {
      const k = Math.min(1, dt * 2.2);
      camera.position.lerp(fly.current.pos, k);
      c.target.lerp(fly.current.target, k);
      if (camera.position.distanceTo(fly.current.pos) < 2) fly.current = null;
    }
    c.autoRotate = performance.now() - lastInput.current > 9000 && !useStore.getState().selected;
    c.update();
  });

  return (
    <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.08} autoRotateSpeed={0.35}
                   maxPolarAngle={Math.PI * 0.47} minDistance={60} maxDistance={3200}
                   onStart={() => { lastInput.current = performance.now(); fly.current = null; }} />
  );
}

function Lights() {
  const site = useStore((s) => s.site);
  return (
    <>
      <ambientLight intensity={0.32} />
      <directionalLight position={[-600, 900, 500]} intensity={1.05} color="#b9c8ff" castShadow
                        shadow-mapSize={[2048, 2048]} shadow-camera-left={-1400} shadow-camera-right={1400}
                        shadow-camera-top={1400} shadow-camera-bottom={-1400} shadow-camera-far={3000} shadow-bias={-0.0005} key={site} />
    </>
  );
}

function Effects() {
  const heat = useStore((s) => !!s.frame?.active_scenarios.includes("heat_wave"));
  const offset = useMemo(() => new THREE.Vector2(0.0018, 0.0012), []);
  return (
    <EffectComposer multisampling={0}>
      <Bloom mipmapBlur intensity={1.15} luminanceThreshold={0.32} luminanceSmoothing={0.25} radius={0.72} />
      <ChromaticAberration offset={heat ? offset : new THREE.Vector2(0, 0)} radialModulation={false} modulationOffset={0} />
      <Noise premultiply blendFunction={BlendFunction.SOFT_LIGHT} opacity={0.18} />
      <Vignette offset={0.22} darkness={0.78} />
    </EffectComposer>
  );
}

export function Scene() {
  const select = useStore((s) => s.select);
  return (
    <Canvas shadows dpr={[1, 1.75]} gl={{ antialias: false, powerPreference: "high-performance" }}
            camera={{ position: CAMERA_HOME.chelsea.pos, fov: 36, near: 2, far: 9000 }}
            onPointerMissed={() => select(null)}>
      <color attach="background" args={["#05070d"]} />
      <Atmosphere />
      <Lights />
      <Ground />
      <ContextCity />
      <Buildings />
      <DataCenter />
      <Pipes />
      <Storage />
      <Snow />
      <CameraRig />
      <Effects />
    </Canvas>
  );
}
