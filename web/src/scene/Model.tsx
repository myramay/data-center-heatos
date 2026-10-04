import { useEffect, useMemo } from "react";
import { useThree } from "@react-three/fiber";
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { useBundle, useStore } from "../store";
import { PLATE } from "./geom";

// Shared "precision model" materials: dark machined metal, satin ivory, amber accents, chrome, copper.
export const MAT = {
  base: new THREE.MeshStandardMaterial({ color: 0x292f37, metalness: 0.85, roughness: 0.32 }),
  edge: new THREE.MeshStandardMaterial({ color: 0x707986, metalness: 0.85, roughness: 0.24 }),
  body: new THREE.MeshStandardMaterial({ color: 0x30363f, metalness: 0.75, roughness: 0.29 }),
  dark: new THREE.MeshStandardMaterial({ color: 0x12171d, metalness: 0.45, roughness: 0.38 }),
  ivory: new THREE.MeshStandardMaterial({ color: 0xd6d3c8, metalness: 0.2, roughness: 0.5 }),
  steel: new THREE.MeshStandardMaterial({ color: 0x8b95a3, metalness: 0.7, roughness: 0.3 }),
  chrome: new THREE.MeshStandardMaterial({ color: 0xc3cad0, metalness: 0.92, roughness: 0.18 }),
  copper: new THREE.MeshStandardMaterial({ color: 0xc57e45, metalness: 0.85, roughness: 0.3 }),
  amberLight: new THREE.MeshStandardMaterial({ color: 0xff7a1a, metalness: 0.2, roughness: 0.25, emissive: 0xff7a1a, emissiveIntensity: 1.5 }),
  glass: new THREE.MeshStandardMaterial({ color: 0x81949e, metalness: 0.45, roughness: 0.16, transparent: true, opacity: 0.19, depthWrite: false }),
};

/** Studio lighting: room-environment reflections + key, rim, warm and front lights. */
export function StudioLights() {
  const { gl, scene } = useThree();
  const site = useStore((s) => s.site);
  const P = PLATE[site];
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl);
    const room = new RoomEnvironment();
    const env = pmrem.fromScene(room, 0.04);
    scene.environment = env.texture;
    scene.environmentIntensity = 0.38;
    gl.toneMappingExposure = 0.95;
    gl.localClippingEnabled = true;
    room.dispose();
    pmrem.dispose();
    return () => { env.texture.dispose(); scene.environment = null; };
  }, [gl, scene]);
  const s = P.half / 10;            // the reference rig is ~10 units across; scale it to the plate
  return (
    <>
      <hemisphereLight args={[0xdbe5f4, 0x29211a, 0.55]} />
      <directionalLight position={[P.cx - 4 * s, 12 * s, P.cz + 7 * s]} intensity={1.7} color={0xfff1d8} castShadow
                        shadow-mapSize={[4096, 4096]} shadow-camera-left={-P.half * 1.3} shadow-camera-right={P.half * 1.3}
                        shadow-camera-top={P.half * 1.3} shadow-camera-bottom={-P.half * 1.3} shadow-camera-near={1}
                        shadow-camera-far={P.half * 6} shadow-normalBias={0.6} shadow-bias={-0.0002}
                        target-position={[P.cx, 0, P.cz]} key={site} />
      <directionalLight position={[P.cx + 3 * s, 7 * s, P.cz - 8 * s]} intensity={0.75} color={0xc4d4ed} />
      <directionalLight position={[P.cx - 4 * s, 5 * s, P.cz + 3 * s]} intensity={0.35} color={0xffbd42} />
      <directionalLight position={[P.cx + 5 * s, 3 * s, P.cz + 10 * s]} intensity={0.25} />
    </>
  );
}

function canvasTexture(w: number, h: number, draw: (c: CanvasRenderingContext2D) => void) {
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  draw(cv.getContext("2d")!);
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

/** The machined plate the model sits on, with an amber edge light, captive screws and an engraved nameplate. */
export function Plate() {
  const site = useStore((s) => s.site);
  const bundle = useBundle();
  const P = PLATE[site];
  const W = P.half * 2, D = P.half * 2, T = P.thick;
  const geos = useMemo(() => ({
    base: new RoundedBoxGeometry(W + T * 0.9, T, D + T * 0.9, 3, T * 0.42),
    lip: new RoundedBoxGeometry(W + T * 0.5, T * 0.15, D + T * 0.5, 2, T * 0.07),
    top: new RoundedBoxGeometry(W + T * 0.2, T * 0.22, D + T * 0.2, 2, T * 0.08),
  }), [W, D, T]);
  const plaque = useMemo(() => canvasTexture(1536, 176, (c) => {
    c.fillStyle = "#252b32"; c.fillRect(0, 0, 1536, 176);
    c.strokeStyle = "#4d545c"; c.lineWidth = 2; c.strokeRect(2, 2, 1532, 172);
    c.font = "650 40px 'Space Grotesk', sans-serif"; c.fillStyle = "#d9d8cd";
    c.fillText("HEATOS", 45, 79);
    c.font = "450 34px 'Space Grotesk', sans-serif"; c.fillStyle = "#b8bdc1";
    c.fillText(`·  ${bundle?.config.name ?? site}  ·  ${bundle?.config.address ?? ""}`.slice(0, 70), 230, 79);
    c.font = "500 19px 'JetBrains Mono', monospace"; c.fillStyle = "#737e88";
    c.fillText("DATA CENTER HEAT REUSE    /    LIVE NETWORK MODEL", 47, 133);
    c.font = "500 23px 'JetBrains Mono', monospace"; c.fillStyle = "#c57e45";
    c.fillText(site === "chelsea" ? "SITE A" : "SITE B", 1380, 130);
  }), [bundle, site]);
  const screws: [number, number][] = [[-1, -1], [1, -1], [-1, 1], [1, 1]];
  return (
    <group position={[P.cx, 0, P.cz]}>
      <mesh geometry={geos.base} material={MAT.base} position={[0, -T * 0.62, 0]} castShadow receiveShadow />
      <mesh geometry={geos.lip} material={MAT.edge} position={[0, -T * 0.08, 0]} receiveShadow />
      <mesh geometry={geos.top} material={MAT.body} position={[0, -T * 0.13, 0]} receiveShadow />
      {/* amber light strip along the front edge */}
      <mesh position={[0, -T * 0.55, D / 2 + T * 0.47]} material={MAT.amberLight}>
        <boxGeometry args={[W * 0.93, T * 0.07, T * 0.06]} />
      </mesh>
      {screws.map(([sx, sz]) => (
        <group key={`${sx}${sz}`} position={[sx * (W / 2 + T * 0.1), -T * 0.02, sz * (D / 2 + T * 0.1)]}>
          <mesh material={MAT.chrome}><cylinderGeometry args={[T * 0.22, T * 0.22, T * 0.1, 16]} /></mesh>
          <mesh material={MAT.dark} position={[0, T * 0.055, 0]}><boxGeometry args={[T * 0.28, T * 0.02, T * 0.04]} /></mesh>
        </group>
      ))}
      {/* engraved nameplate on the front of the deck */}
      <mesh position={[-W * 0.12, 0.6, D / 2 - D * 0.045]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[W * 0.56, W * 0.064]} />
        <meshBasicMaterial map={plaque} toneMapped={false} />
      </mesh>
    </group>
  );
}

/** Shadow-catching floor, soft contact shadow and faint drafting rings around the plate. */
export function Floor() {
  const site = useStore((s) => s.site);
  const P = PLATE[site];
  const y = -P.thick * 1.15;
  const contact = useMemo(() => canvasTexture(128, 128, (c) => {
    const g = c.createRadialGradient(64, 64, 12, 64, 64, 64);
    g.addColorStop(0, "rgba(0,0,0,.8)"); g.addColorStop(0.55, "rgba(0,0,0,.45)"); g.addColorStop(1, "rgba(0,0,0,0)");
    c.fillStyle = g; c.fillRect(0, 0, 128, 128);
  }), []);
  const rings = useMemo(() => {
    const pts: THREE.Vector3[] = [];
    for (const r of [1.5, 1.62]) for (let i = 0; i < 160; i++) for (const j of [i, i + 1]) {
      const a = (j / 160) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a) * r * P.half, 0, Math.sin(a) * r * P.half * 0.72));
    }
    for (let i = 0; i < 64; i++) {
      const a = (i / 64) * Math.PI * 2, r = 1.62 * P.half, k = i % 4 === 0 ? 0.035 : 0.015;
      pts.push(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r * 0.72),
               new THREE.Vector3(Math.cos(a) * r * (1 + k), 0, Math.sin(a) * r * (1 + k) * 0.72));
    }
    return new THREE.BufferGeometry().setFromPoints(pts);
  }, [P.half]);
  return (
    <group position={[P.cx, y, P.cz]}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[P.half * 12, P.half * 12]} />
        <shadowMaterial opacity={0.23} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.5, 0]}>
        <planeGeometry args={[P.half * 2.9, P.half * 2.3]} />
        <meshBasicMaterial map={contact} transparent depthWrite={false} opacity={0.65} />
      </mesh>
      <lineSegments geometry={rings} position={[0, 0.8, 0]}>
        <lineBasicMaterial color={0x69717a} transparent opacity={0.16} />
      </lineSegments>
    </group>
  );
}

/** Clipping planes that keep map tiles and context inside the plate. */
export function plateClip(site: "chelsea" | "lansing"): THREE.Plane[] {
  const P = PLATE[site];
  return [
    new THREE.Plane(new THREE.Vector3(1, 0, 0), -(P.cx - P.half)),
    new THREE.Plane(new THREE.Vector3(-1, 0, 0), P.cx + P.half),
    new THREE.Plane(new THREE.Vector3(0, 0, 1), -(P.cz - P.half)),
    new THREE.Plane(new THREE.Vector3(0, 0, -1), P.cz + P.half),
  ];
}
