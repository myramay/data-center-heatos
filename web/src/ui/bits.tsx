import type { ReactNode } from "react";
import { motion } from "framer-motion";
import type { Verdict } from "../types";

export function Panel({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`glass flex flex-col min-h-0 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between px-4 pt-3 pb-2">
          <div className="label">{title}</div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

const polar = (cx: number, cy: number, r: number, deg: number) => {
  const a = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)] as const;
};
const arc = (cx: number, cy: number, r: number, a0: number, a1: number) => {
  const [x0, y0] = polar(cx, cy, r, a0);
  const [x1, y1] = polar(cx, cy, r, a1);
  return `M ${x0} ${y0} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1} ${y1}`;
};

/** 240° radial gauge; primary arc = Monte Carlo, optional second needle = Jev. */
export function Gauge({ value, second, size = 200, label, secondLabel }: {
  value: number | null; second?: number | null; size?: number; label: string; secondLabel?: string;
}) {
  const cx = size / 2, cy = size / 2 + 8, r = size * 0.4, A0 = -120, A1 = 120;
  const v = value ?? 0;
  const color = v >= 0.9 ? "#0ca30c" : v >= 0.7 ? "#fab219" : "#d03b3b";
  const sAng = second != null ? A0 + (A1 - A0) * second : null;
  return (
    <svg width={size} height={size * 0.82} viewBox={`0 0 ${size} ${size * 0.82}`} role="img"
         aria-label={`${label} ${value == null ? "pending" : Math.round(v * 100) + "%"}`}>
      <path d={arc(cx, cy, r, A0, A1)} stroke="rgb(148 163 184 / 0.16)" strokeWidth={12} fill="none" strokeLinecap="round" />
      {[0.7, 0.9].map((t) => {
        const [x0, y0] = polar(cx, cy, r - 10, A0 + (A1 - A0) * t);
        const [x1, y1] = polar(cx, cy, r + 10, A0 + (A1 - A0) * t);
        return <line key={t} x1={x0} y1={y0} x2={x1} y2={y1} stroke="rgb(148 163 184 / 0.45)" strokeWidth={1.5} />;
      })}
      {value != null && (
        <motion.path d={arc(cx, cy, r, A0, A1)} stroke={color} strokeWidth={12} fill="none" strokeLinecap="round"
                     initial={false} animate={{ pathLength: Math.max(0.002, v), stroke: color }} transition={{ duration: 0.6 }}
                     style={{ filter: `drop-shadow(0 0 8px ${color}88)` }} />
      )}
      {sAng != null && (() => {
        const [x, y] = polar(cx, cy, r - 18, sAng);
        const [x2, y2] = polar(cx, cy, r + 14, sAng);
        return <motion.line x1={x} y1={y} x2={x2} y2={y2} stroke="#c3b8ff" strokeWidth={3} strokeLinecap="round"
                            initial={false} animate={{ x1: x, y1: y, x2, y2 }} transition={{ duration: 0.6 }} />;
      })()}
      <text x={cx} y={cy - 4} textAnchor="middle" className="num" fill="#e8edf7" fontSize={size * 0.19} fontWeight={600}>
        {value == null ? "…" : `${Math.round(v * 100)}%`}
      </text>
      {second != null && secondLabel && (
        <text x={cx} y={cy + size * 0.12} textAnchor="middle" fill="#c3b8ff" fontSize={11} className="num">
          {secondLabel} {Math.round(second * 100)}%
        </text>
      )}
    </svg>
  );
}

export function Sparkline({ values, band = 0.15, width = 64, height = 18, color = "#3987e5" }: {
  values: number[]; band?: number; width?: number; height?: number; color?: string;
}) {
  if (values.length < 2) return <svg width={width} height={height} />;
  const max = Math.max(...values) * (1 + band) || 1;
  const x = (i: number) => (i / (values.length - 1)) * width;
  const y = (v: number) => height - (v / max) * (height - 2) - 1;
  const hi = values.map((v, i) => `${x(i)},${y(v * (1 + band))}`).join(" ");
  const lo = values.map((v, i) => `${x(i)},${y(v * (1 - band))}`).reverse().join(" ");
  return (
    <svg width={width} height={height} aria-hidden>
      <polygon points={`${hi} ${lo}`} fill={color} opacity={0.18} />
      <polyline points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}

const VERDICT_STYLE: Record<Verdict, { bg: string; fg: string; icon: string; text: string }> = {
  ACT: { bg: "rgb(12 163 12 / 0.16)", fg: "#4ade80", icon: "✓", text: "ACT" },
  REVIEW: { bg: "rgb(250 178 25 / 0.16)", fg: "#fab219", icon: "◐", text: "REVIEW" },
  ESCALATE: { bg: "rgb(208 59 59 / 0.2)", fg: "#ff6b6b", icon: "▲", text: "ESCALATE" },
};
export function VerdictBadge({ v }: { v: Verdict | null }) {
  if (!v) return <span className="label">verdict pending</span>;
  const s = VERDICT_STYLE[v];
  return (
    <motion.span key={v} initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                 className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[12px] font-semibold tracking-wider"
                 style={{ background: s.bg, color: s.fg, border: `1px solid ${s.fg}55` }}>
      <span aria-hidden>{s.icon}</span>{s.text}
    </motion.span>
  );
}

export function Bar({ value, color = "#3987e5", height = 4 }: { value: number; color?: string; height?: number }) {
  return (
    <div className="w-full rounded-full" style={{ height, background: "rgb(148 163 184 / 0.14)" }}>
      <motion.div className="rounded-full" style={{ height, background: color }} initial={false}
                  animate={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} transition={{ duration: 0.5 }} />
    </div>
  );
}

export function Chip({ children, color = "#a3adc2" }: { children: ReactNode; color?: string }) {
  return (
    <span className="inline-flex items-center px-1.5 py-[1px] rounded text-[10px] font-medium whitespace-nowrap"
          style={{ color, background: `${color}1f`, border: `1px solid ${color}33` }}>{children}</span>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="num text-[18px] font-semibold text-ink mt-0.5">{value}</div>
      {sub && <div className="text-[11px] text-ink-3 mt-0.5">{sub}</div>}
    </div>
  );
}
