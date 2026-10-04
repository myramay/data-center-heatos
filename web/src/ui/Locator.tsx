import { useMemo } from "react";
import { useStore } from "../store";
import { useGeo } from "../scene/Terrain";
import { PLATE, SITE_SCALE } from "../scene/geom";

const TITLE = { chelsea: ["Chelsea, Manhattan", "New York City"], lansing: ["Lansing, Tompkins County", "Upstate New York · Cayuga Lake"] };

/** Small orientation map: where the model sits in the city / region. */
export function Locator() {
  const site = useStore((s) => s.site);
  const geo = useGeo(site);
  const size = 168;
  const paths = useMemo(() => {
    if (!geo) return null;
    const R = geo.extent_m * 0.8;
    const sx = (e: number) => ((e + R) / (2 * R)) * size;
    const sy = (n: number) => ((R - n) / (2 * R)) * size;
    const d = (rings: [number, number][][]) => rings.map((r) => "M" + r.map(([e, n]) => `${sx(e).toFixed(1)},${sy(n).toFixed(1)}`).join("L") + "Z").join("");
    const P = PLATE[site], s = SITE_SCALE[site];
    const plate = { x: sx((P.cx - P.half) / s), y: sy(-(P.cz - P.half) / s), w: sx((P.cx + P.half) / s) - sx((P.cx - P.half) / s) };
    return { land: d(geo.land), water: d(geo.water), pin: [sx(0), sy(0)] as const, plate };
  }, [geo, site]);
  return (
    <div className="glass absolute left-3 top-[76px] z-20 p-2 w-[184px]" style={{ borderRadius: 12 }}>
      <div className="px-1 pb-1.5">
        <div className="text-[11.5px] font-semibold text-ink leading-tight">{TITLE[site][0]}</div>
        <div className="text-[10px] text-ink-3">{TITLE[site][1]}</div>
      </div>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="rounded-md block" style={{ background: "#8fa7b8" }} role="img" aria-label={`Locator map: ${TITLE[site].join(", ")}`}>
        {paths && (
          <>
            <path d={paths.land} fill="#e3dfd4" fillRule="evenodd" />
            <path d={paths.water} fill="#8fa7b8" />
            <rect x={paths.plate.x} y={paths.plate.y} width={paths.plate.w} height={paths.plate.w} fill="none" stroke="#ff7a1a" strokeWidth={1.5} strokeDasharray="3 2" />
            <circle cx={paths.pin[0]} cy={paths.pin[1]} r={4} fill="#ff7a1a" stroke="#fff" strokeWidth={1.5} />
          </>
        )}
      </svg>
      <div className="text-[9.5px] text-ink-3 px-1 pt-1">orange box = the 3D model · dot = data center</div>
    </div>
  );
}
