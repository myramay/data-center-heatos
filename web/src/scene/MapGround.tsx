import { useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import { useStore } from "../store";
import { SITE_SCALE, enToWorld, lonLatToEN, ORIGIN } from "./geom";
import type { SiteId } from "../types";

// CARTO Voyager raster tiles (OpenStreetMap data). Attribution is shown in the UI.
const TILE_URL = (z: number, x: number, y: number) => `/tiles/${z}/${x}/${y}@2x.png`;   // proxied in vite.config.ts
const ZOOM: Record<SiteId, number> = { chelsea: 16, lansing: 14 };
const RADIUS_M: Record<SiteId, number> = { chelsea: 1700, lansing: 7500 };

const lon2x = (lon: number, z: number) => ((lon + 180) / 360) * 2 ** z;
const lat2y = (lat: number, z: number) => {
  const r = (lat * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z;
};
const x2lon = (x: number, z: number) => (x / 2 ** z) * 360 - 180;
const y2lat = (y: number, z: number) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / 2 ** z))) * 180) / Math.PI;

function Tile({ site, z, x, y }: { site: SiteId; z: number; x: number; y: number }) {
  const [tex, setTex] = useState<THREE.Texture | null>(null);
  const geo = useMemo(() => {
    const [w, n] = lonLatToEN(site, x2lon(x, z), y2lat(y, z));
    const [e, s] = lonLatToEN(site, x2lon(x + 1, z), y2lat(y + 1, z));
    const [x0, , z0] = enToWorld(site, w, n);
    const [x1, , z1] = enToWorld(site, e, s);
    return { cx: (x0 + x1) / 2, cz: (z0 + z1) / 2, w: x1 - x0, d: z1 - z0 };
  }, [site, z, x, y]);
  useEffect(() => {
    let alive = true;
    const loader = new THREE.TextureLoader();
    loader.setCrossOrigin("anonymous");
    loader.load(TILE_URL(z, x, y), (t) => {
      if (!alive) return;
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = 8;
      setTex(t);
    }, undefined, () => {});
    return () => { alive = false; };
  }, [z, x, y]);
  if (!tex) return null;
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[geo.cx, 0.02, geo.cz]} receiveShadow raycast={() => null}>
      <planeGeometry args={[geo.w + 0.05, geo.d + 0.05]} />
      <meshStandardMaterial map={tex} roughness={1} metalness={0} />
    </mesh>
  );
}

/** Real map under the 3D scene, placed by latitude/longitude. */
export function MapGround() {
  const site = useStore((s) => s.site);
  const tiles = useMemo(() => {
    const z = ZOOM[site];
    const [lat0, lon0] = ORIGIN[site];
    const dLat = RADIUS_M[site] / 111_320;
    const dLon = RADIUS_M[site] / (111_320 * Math.cos((lat0 * Math.PI) / 180));
    const x0 = Math.floor(lon2x(lon0 - dLon, z)), x1 = Math.floor(lon2x(lon0 + dLon, z));
    const y0 = Math.floor(lat2y(lat0 + dLat, z)), y1 = Math.floor(lat2y(lat0 - dLat, z));
    const out: { z: number; x: number; y: number }[] = [];
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) out.push({ z, x, y });
    return out;
  }, [site]);
  const s = SITE_SCALE[site];
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.3, 0]} receiveShadow>
        <planeGeometry args={[RADIUS_M[site] * s * 6, RADIUS_M[site] * s * 6]} />
        <meshStandardMaterial color="#e9edf1" roughness={1} />
      </mesh>
      {tiles.map((t) => <Tile key={`${site}-${t.z}-${t.x}-${t.y}`} site={site} {...t} />)}
    </group>
  );
}
