import { useEffect, useMemo, useState } from "react";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { useStore } from "../store";
import { PLATE, SITE_SCALE, HEIGHT_EXAG, enToWorld, lonLatToEN } from "./geom";
import { MAT, plateClip } from "./Model";
import type { SiteId } from "../types";

// Geography drawn from files fetched once by scripts/fetch_geo.py (US Census land + water, OSM skyline).
export interface Geo { extent_m: number; land: [number, number][][]; water: [number, number][][]; skyline: { h: number; p: [number, number][]; n?: string }[] }

const cache = new Map<SiteId, Promise<Geo | null>>();
export function useGeo(site: SiteId) {
  const [geo, setGeo] = useState<Geo | null>(null);
  useEffect(() => {
    if (!cache.has(site)) cache.set(site, fetch(`/geo/${site}_geo.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null));
    let alive = true;
    cache.get(site)!.then((g) => { if (alive) setGeo(g); });
    return () => { alive = false; };
  }, [site]);
  return geo;
}

export const PLACES: Record<SiteId, { name: string; lat: number; lon: number; kind: "water" | "area" | "landmark" }[]> = {
  chelsea: [
    { name: "Hudson River", lat: 40.7435, lon: -74.0155, kind: "water" },
    { name: "New Jersey", lat: 40.7455, lon: -74.0400, kind: "area" },
    { name: "Hoboken", lat: 40.7440, lon: -74.0300, kind: "area" },
    { name: "Manhattan", lat: 40.7720, lon: -73.9700, kind: "area" },
    { name: "Chelsea", lat: 40.7465, lon: -73.9990, kind: "area" },
    { name: "Midtown", lat: 40.7560, lon: -73.9830, kind: "area" },
    { name: "Hudson Yards", lat: 40.7540, lon: -74.0020, kind: "landmark" },
    { name: "Empire State Building", lat: 40.7484, lon: -73.9857, kind: "landmark" },
    { name: "One World Trade Center", lat: 40.7127, lon: -74.0134, kind: "landmark" },
    { name: "East River", lat: 40.7330, lon: -73.9690, kind: "water" },
    { name: "Brooklyn", lat: 40.6930, lon: -73.9660, kind: "area" },
    { name: "Upper New York Bay", lat: 40.6750, lon: -74.0450, kind: "water" },
  ],
  lansing: [
    { name: "Cayuga Lake", lat: 42.6100, lon: -76.6700, kind: "water" },
    { name: "Town of Lansing", lat: 42.6150, lon: -76.5800, kind: "area" },
    { name: "Former Cayuga coal plant", lat: 42.6025, lon: -76.6338, kind: "landmark" },
    { name: "↓ Ithaca (19 km)", lat: 42.5200, lon: -76.5900, kind: "area" },
  ],
};

const LAND_DEPTH = 8;

function shapes(site: SiteId, rings: [number, number][][], depth: number, minArea = 0): THREE.BufferGeometry | null {
  const s = SITE_SCALE[site];
  const geos: THREE.BufferGeometry[] = [];
  for (const ring of rings) {
    if (ring.length < 4) continue;
    const shape = new THREE.Shape(ring.map(([e, n]) => new THREE.Vector2(e * s, n * s)));
    if (Math.abs(THREE.ShapeUtils.area(shape.getPoints())) < minArea) continue;
    const g = depth > 0 ? new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false, curveSegments: 1 }) : new THREE.ShapeGeometry(shape);
    g.rotateX(-Math.PI / 2);
    geos.push(depth > 0 ? g.toNonIndexed() : g.toNonIndexed());
    g.dispose();
  }
  if (!geos.length) return null;
  const m = mergeGeometries(geos, false);
  geos.forEach((g) => g.dispose());
  m?.computeVertexNormals();
  return m;
}

// Water sits clearly above land with a polygon offset, so the two never z-fight at a distance.
const WATER = new THREE.MeshStandardMaterial({ color: 0x8fa7b8, metalness: 0.35, roughness: 0.32,
  polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4 });
const WATER_BASE = new THREE.MeshStandardMaterial({ color: 0x8fa7b8, metalness: 0.35, roughness: 0.32 });
const LAND = new THREE.MeshStandardMaterial({ color: 0xe3dfd4, metalness: 0.08, roughness: 0.8 });

/** The surrounding city/region: water table, land, rivers, skyline; plus the same map printed on the plate. */
export function Terrain() {
  const site = useStore((s) => s.site);
  const geo = useGeo(site);
  const P = PLATE[site];
  const floor = -P.thick * 1.15;
  const s = SITE_SCALE[site];

  const g = useMemo(() => {
    if (!geo) return null;
    const land = shapes(site, geo.land, LAND_DEPTH * Math.max(s, 0.5));
    const water = shapes(site, geo.water, 0, 400 * s * s);
    const sky: THREE.BufferGeometry[] = [];
    for (const t of geo.skyline) {
      const cx = t.p.reduce((a, q) => a + q[0], 0) / t.p.length, cy = t.p.reduce((a, q) => a + q[1], 0) / t.p.length;
      const [wx, , wz] = enToWorld(site, cx, cy);
      if (Math.abs(wx - P.cx) < P.half + 30 && Math.abs(wz - P.cz) < P.half + 30) continue;   // the plate shows its own buildings
      const shape = new THREE.Shape(t.p.map(([e, n]) => new THREE.Vector2(e * s, n * s)));
      const eg = new THREE.ExtrudeGeometry(shape, { depth: t.h * s * HEIGHT_EXAG[site], bevelEnabled: false, curveSegments: 1 });
      eg.rotateX(-Math.PI / 2);
      sky.push(eg.toNonIndexed());
      eg.dispose();
    }
    const skyline = sky.length ? mergeGeometries(sky, false) : null;
    sky.forEach((x) => x.dispose());
    skyline?.computeVertexNormals();
    return { land, water, skyline };
  }, [geo, site, s, P.cx, P.cz, P.half]);

  const clip = useMemo(() => plateClip(site), [site]);
  const printLand = useMemo(() => { const m = LAND.clone(); m.clippingPlanes = clip; m.color.set(0xe9e6dd); return m; }, [clip]);
  const printWater = useMemo(() => { const m = WATER.clone(); m.clippingPlanes = clip; return m; }, [clip]);
  if (!geo || !g) return null;
  const R = geo.extent_m * s;
  const landTop = floor + 2 + LAND_DEPTH * Math.max(s, 0.5);
  return (
    <group>
      {/* the region around the model, at floor level */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, floor - 3, 0]} material={WATER_BASE} receiveShadow>
        <circleGeometry args={[R * 1.05, 96]} />
      </mesh>
      {g.land && <mesh geometry={g.land} material={LAND} position={[0, floor + 2, 0]} receiveShadow />}
      {g.water && <mesh geometry={g.water} material={WATER} position={[0, landTop + 2.5, 0]} receiveShadow />}
      {g.skyline && <mesh geometry={g.skyline} material={MAT.ivory} position={[0, landTop, 0]} castShadow receiveShadow />}
      {/* the same map printed on the plate */}
      {g.land && <mesh geometry={g.land} material={printLand} position={[0, 0.1 - LAND_DEPTH * Math.max(s, 0.5), 0]} />}
      {g.water && <mesh geometry={g.water} material={printWater} position={[0, 1.2, 0]} />}
      <PlaceLabels site={site} floorY={landTop} />
    </group>
  );
}

function PlaceLabels({ site, floorY }: { site: SiteId; floorY: number }) {
  const P = PLATE[site];
  return (
    <>
      {PLACES[site].map((pl) => {
        const [e, n] = lonLatToEN(site, pl.lon, pl.lat);
        const [x, , z] = enToWorld(site, e, n);
        const onPlate = Math.abs(x - P.cx) < P.half && Math.abs(z - P.cz) < P.half;
        const y = (onPlate ? 2 : floorY) + (pl.kind === "landmark" ? 40 * SITE_SCALE[site] + 30 : 4);
        const style = pl.kind === "water"
          ? { color: "#3f5f78", fontStyle: "italic", letterSpacing: "0.22em" }
          : pl.kind === "landmark" ? { color: "#9a5a1f", letterSpacing: "0.08em" } : { color: "#4b5260", letterSpacing: "0.3em" };
        return (
          <Html key={pl.name} position={[x, y, z]} center zIndexRange={[4, 0]} style={{ pointerEvents: "none" }}>
            <div className="whitespace-nowrap uppercase font-semibold" style={{ fontSize: pl.kind === "area" ? 11.5 : 10.5, textShadow: "0 1px 0 rgb(255 255 255 / 0.7)", ...style }}>
              {pl.kind === "landmark" && <span className="inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle" style={{ background: "#c57e45" }} />}
              {pl.name}
            </div>
          </Html>
        );
      })}
    </>
  );
}
