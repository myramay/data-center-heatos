import { useMemo } from "react";
import { motion } from "framer-motion";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useBundle, useStore } from "../store";
import { Chip } from "./bits";
import { num, partyColor, usd } from "../lib/format";
import type { Explanation, Physics, TreeNode } from "../frame";

// ===================================================================== physics

const kwW = (kw: number, max: number) => Math.max(1.5, Math.min(18, (kw / Math.max(max, 1)) * 18));

function FlowLink({ d, kw, max, color, label, speed = 1 }: { d: string; kw: number; max: number; color: string; label?: string; speed?: number }) {
  const w = kwW(kw, max);
  const on = kw > 1;
  return (
    <g opacity={on ? 1 : 0.25}>
      <path d={d} fill="none" stroke={color} strokeOpacity={0.28} strokeWidth={w} strokeLinecap="round" />
      {on && (
        <path d={d} fill="none" stroke={color} strokeWidth={Math.max(1.5, w * 0.45)} strokeLinecap="round" strokeDasharray="6 10">
          <animate attributeName="stroke-dashoffset" from="32" to="0" dur={`${Math.max(0.35, 1.6 / speed)}s`} repeatCount="indefinite" />
        </path>
      )}
      {label && <title>{label}</title>}
    </g>
  );
}

function Node({ x, y, w = 128, h = 58, title, value, sub, accent = "#3987e5" }: {
  x: number; y: number; w?: number; h?: number; title: string; value: string; sub?: string; accent?: string;
}) {
  return (
    <g transform={`translate(${x},${y})`}>
      <rect width={w} height={h} rx={9} fill="#0f1628" stroke={accent} strokeOpacity={0.55} />
      <rect width={3} height={h - 16} x={0} y={8} rx={1.5} fill={accent} />
      <text x={12} y={18} fill="#a3adc2" fontSize={10} letterSpacing={0.6}>{title.toUpperCase()}</text>
      <text x={12} y={37} fill="#e8edf7" fontSize={15} fontWeight={600} className="num">{value}</text>
      {sub && <text x={12} y={51} fill="#6b7690" fontSize={9.5} className="num">{sub}</text>}
    </g>
  );
}

export function PhysicsView() {
  const f = useStore((s) => s.frame);
  const frames = useStore((s) => s.frames);
  const bundle = useBundle();
  const p: Physics | undefined = f?.physics;
  const temps = useMemo(() => frames.slice(-72).filter((x) => x.physics).map((x) => {
    const d = new Date(x.time);
    return { t: `${d.toLocaleDateString("en-US", { weekday: "short" })} ${String(d.getHours()).padStart(2, "0")}h`,
             supply: x.physics!.loop_supply_c, ret: x.physics!.loop_return_c, outdoor: x.physics!.t_out_c, cop: x.physics!.cop_avg };
  }), [frames]);
  if (!p) return <div className="text-[12px] text-ink-3">Start the simulation (Stress-test step) to see the network physics live.{f && " This recording predates the physics feed; run `make record`."}</div>;
  const max = Math.max(p.demand_kw, p.dc_used_kw + p.dc_fallback_kw, 1);
  const soc = f?.storage.reduce((a, s) => a + s.soc_mwh, 0) ?? 0;
  const cap = bundle?.config.storage.reduce((a, s) => a + s.capacity_mwh, 0) ?? 1;
  const ambient = bundle?.config.loop.type === "ambient_two_way";
  const speed = Math.max(0.3, p.flow_m3h / 300);
  const err = p.balance_error * 100;
  return (
    <div>
      <div className="text-[12px] text-ink-2 mb-2">
        Every arrow is this hour's heat flow from the simulator (width ∝ kW). Heat pumps add electricity to loop heat:
        <span className="num"> delivered = loop heat + electricity</span>, with COP = delivered ÷ electricity.
      </div>
      <svg viewBox="0 0 720 330" className="w-full" role="img" aria-label="Live energy flows through the heat network">
        {/* links */}
        <FlowLink d="M148 150 C 165 150, 165 150, 182 150" kw={p.dc_used_kw + p.dc_fallback_kw} max={max} color="#ff8a3d" speed={speed} />
        <FlowLink d="M246 179 C 246 240, 200 270, 150 285" kw={p.dc_fallback_kw} max={max} color="#8a94ab" speed={speed} label="heat the data center's own cooling rejects" />
        <FlowLink d="M310 150 C 330 150, 330 150, 352 150" kw={p.dc_used_kw} max={max} color="#ff8a3d" speed={speed} />
        {ambient && <FlowLink d="M246 60 C 300 60, 330 90, 400 121" kw={p.cooling_in_kw} max={max} color="#67e8f9" speed={speed} label="office cooling heat rejected into the loop" />}
        <FlowLink d="M416 179 C 416 220, 416 230, 416 250" kw={p.storage_in_kw} max={max} color="#199e70" speed={speed} label="charging storage" />
        <FlowLink d="M440 250 C 440 230, 440 220, 440 179" kw={p.storage_out_kw} max={max} color="#199e70" speed={speed} label="discharging storage" />
        <FlowLink d="M480 150 C 500 150, 500 150, 522 150" kw={Math.max(p.delivered_kw - p.hp_elec_kw, 0)} max={max} color="#3987e5" speed={speed} />
        <FlowLink d="M586 60 C 586 90, 586 100, 586 121" kw={p.hp_elec_kw} max={max} color="#c3b8ff" speed={speed} label="heat pump electricity" />
        <FlowLink d="M650 150 C 670 150, 670 150, 690 150" kw={p.delivered_kw} max={max} color="#3987e5" speed={speed} />
        <FlowLink d="M586 280 C 640 280, 690 240, 700 179" kw={p.backup_kw} max={max} color="#d95926" speed={speed} label="existing boilers" />
        <FlowLink d="M416 121 C 416 100, 416 95, 416 80" kw={p.pipe_loss_kw * 20} max={max} color="#6b7690" speed={speed} label="pipe losses (x20 for visibility)" />
        {/* nodes */}
        <Node x={20} y={121} title="Data center" value={`${num(p.dc_used_kw + p.dc_fallback_kw)} kW`} sub={`water ${p.dc_supply_c}°C`} accent="#ff8a3d" />
        <Node x={182} y={121} title="Heat exchanger" value={`${num(p.dc_used_kw)} kW`} sub="taken by the network" accent="#ff8a3d" />
        <Node x={20} y={262} title="Own cooling" value={`${num(p.dc_fallback_kw)} kW`} sub="data center always protected" accent="#8a94ab" />
        {ambient && <Node x={118} y={32} title="Office cooling in" value={`${num(p.cooling_in_kw)} kW`} sub="two-way loop" accent="#67e8f9" />}
        <Node x={352} y={121} title={ambient ? "Ambient loop" : "Warm loop"} value={`${p.loop_supply_c}° → ${p.loop_return_c}°C`} sub={`${num(p.flow_m3h)} m³/h · pump ${num(p.pump_kw)} kW`} accent="#3987e5" />
        <Node x={352} y={250} title="Storage" value={`${Math.round((soc / cap) * 100)}% full`} sub={`${p.storage_out_kw > 1 ? `out ${num(p.storage_out_kw)}` : `in ${num(p.storage_in_kw)}`} kW`} accent="#199e70" />
        <Node x={352} y={30} w={128} h={48} title="Pipe losses" value={`${num(p.pipe_loss_kw, 1)} kW`} accent="#6b7690" />
        <Node x={522} y={121} title="Heat pumps" value={p.cop_avg ? `COP ${p.cop_avg.toFixed(2)}` : "direct use"} sub="lift loop heat to building temp." accent="#c3b8ff" />
        <Node x={522} y={18} w={128} h={44} title="Grid electricity" value={`${num(p.hp_elec_kw)} kW`} accent="#c3b8ff" />
        <Node x={522} y={252} title="Existing boilers" value={`${num(p.backup_kw)} kW`} sub="backup, by building" accent="#d95926" />
        <g transform="translate(588,121)">
          <rect width={128} height={58} rx={9} fill="#16120d" stroke="#ff8a3d" strokeOpacity={0.6} />
          <text x={12} y={18} fill="#a3adc2" fontSize={10} letterSpacing={0.6}>BUILDINGS</text>
          <text x={12} y={37} fill="#e8edf7" fontSize={15} fontWeight={600} className="num">{num(p.demand_kw)} kW</text>
          <text x={12} y={51} fill="#6b7690" fontSize={9.5}>{num(p.delivered_kw)} from network</text>
        </g>
      </svg>

      <div className="mt-2 p-3 rounded-lg border border-line bg-white/[0.02]">
        <div className="flex items-center justify-between">
          <div className="label">Energy balance this hour (checked every hour)</div>
          <span className="text-[11px] font-semibold" style={{ color: err <= 0.1 ? "#4ade80" : "#ff6b6b" }}>{err <= 0.1 ? "✓ closes" : "▲ off"} · error {err.toFixed(4)}%</span>
        </div>
        <div className="num text-[11.5px] text-ink-2 mt-1.5 leading-relaxed">
          data center {num(p.dc_used_kw)} + cooling in {num(p.cooling_in_kw)} + storage out {num(p.storage_out_kw)} + heat-pump electricity {num(p.hp_elec_kw)} + boilers {num(p.backup_kw)}
          <span className="text-ink"> = {num(p.balance_in_kw)} kW</span>
          <br />demand {num(p.demand_kw)} + pipe losses {num(p.pipe_loss_kw, 1)} + storage in {num(p.storage_in_kw)}
          <span className="text-ink"> = {num(p.balance_out_kw)} kW</span>
        </div>
      </div>

      <div className="label mt-4">Loop temperatures · last 72 h (°C)</div>
      <div className="h-[150px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={temps} margin={{ top: 6, right: 10, bottom: 0, left: -18 }}>
            <CartesianGrid stroke="rgb(148 163 184 / 0.08)" vertical={false} />
            <XAxis dataKey="t" tick={{ fill: "#6b7690", fontSize: 10 }} tickLine={false} interval={11} />
            <YAxis tick={{ fill: "#6b7690", fontSize: 10 }} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={{ background: "#0b1020", border: "1px solid rgb(148 163 184 / 0.2)", borderRadius: 8, fontSize: 11 }} formatter={(v) => `${Number(v).toFixed(1)}°C`} />
            <Legend iconType="circle" iconSize={7} wrapperStyle={{ fontSize: 10.5 }} verticalAlign="top" height={18} />
            <Line dataKey="supply" name="Loop supply" stroke="#ff8a3d" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="ret" name="Loop return" stroke="#3987e5" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="outdoor" name="Outdoor" stroke="#8a94ab" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ===================================================================== compare

function HBars({ rows, unit, fmt }: { rows: { key: string; label: string; value: number; best?: boolean }[]; unit: string; fmt: (v: number) => string }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1);
  return (
    <div className="space-y-1.5">
      {rows.map((r) => (
        <div key={r.key} className="flex items-center gap-2 text-[11px]">
          <span className={`w-40 shrink-0 truncate ${r.key === "heatos" ? "text-ink font-semibold" : "text-ink-2"}`}>{r.label}</span>
          <div className="flex-1 h-4 rounded bg-white/5 relative overflow-hidden">
            <motion.div className="absolute left-0 top-0 bottom-0 rounded" initial={false}
                        animate={{ width: `${(Math.max(r.value, 0) / max) * 100}%` }}
                        style={{ background: r.key === "heatos" ? "#ff8a3d" : "#5b6b85" }} />
          </div>
          <span className="num w-24 text-right text-ink">{fmt(r.value)}{unit}</span>
          <span className="w-4 text-[11px]" style={{ color: "#4ade80" }}>{r.best ? "★" : ""}</span>
        </div>
      ))}
    </div>
  );
}

function Waterfall({ steps }: { steps: { step: string; t: number; total?: boolean }[] }) {
  if (steps.length < 2) return null;
  const W = 660, H = 170, pad = 30;
  let run = 0;
  const bars = steps.map((s, i) => {
    const start = s.total || i === 0 ? 0 : run;
    const end = s.total || i === 0 ? s.t : run + s.t;
    run = s.total ? s.t : end;
    return { ...s, y0: Math.min(start, end), y1: Math.max(start, end), up: end >= start };
  });
  const lo = Math.min(0, ...bars.map((b) => b.y0)), hi = Math.max(...bars.map((b) => b.y1), 1);
  const y = (v: number) => H - pad - ((v - lo) / (hi - lo)) * (H - pad - 12);
  const bw = (W - 20) / bars.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Carbon footprint waterfall">
      <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="rgb(148 163 184 / 0.3)" />
      {bars.map((b, i) => {
        const color = b.total || i === 0 ? "#8a94ab" : b.up ? "#d95926" : "#199e70";
        return (
          <g key={b.step}>
            <rect x={10 + i * bw + 8} y={y(b.y1)} width={bw - 16} height={Math.max(1, y(b.y0) - y(b.y1))} rx={3} fill={color} />
            <text x={10 + i * bw + bw / 2} y={y(b.y1) - 4} textAnchor="middle" fill="#e8edf7" fontSize={10.5} className="num">
              {i === 0 || b.total ? num(b.t) : `${b.t > 0 ? "+" : "−"}${num(Math.abs(b.t))}`}
            </text>
            <text x={10 + i * bw + bw / 2} y={H - 12} textAnchor="middle" fill="#a3adc2" fontSize={9.5}>{b.step.length > 22 ? b.step.slice(0, 21) + "…" : b.step}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function CompareView() {
  const bundle = useBundle();
  const A = bundle?.alternatives;
  if (!A || !A.alternatives) return <div className="text-[12px] text-ink-3">The comparison is precomputed by <code>make record</code> (or GET /alternatives on the live API).</div>;
  const parties = bundle!.config.parties;
  const alts = A.alternatives;
  const heatos = alts.find((a) => a.key === "heatos")!;
  const social = A.social_cost_usd_per_yr;
  const minCost = Math.min(...Object.values(social));
  const minCo2 = Math.min(...alts.map((a) => a.co2_t_per_yr));
  return (
    <div>
      <div className="p-3 rounded-lg mb-3" style={{ background: A.verdict.heatos_lowest_social_cost ? "rgb(12 163 12 / 0.1)" : "rgb(250 178 25 / 0.08)",
                                                     border: `1px solid ${A.verdict.heatos_lowest_social_cost ? "#0ca30c55" : "#fab21955"}` }}>
        <div className="text-[14px] font-semibold">{A.verdict.summary}</div>
        <div className="text-[11.5px] text-ink-2 mt-1">
          Every option is scored over the same {A.universe_buildings} candidate buildings (buildings an option does not serve keep today's heating),
          with one simulated year each. Carbon is valued at ${num(A.carbon_price_usd_per_t)}/t.
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="label mb-2">Total cost to society · $/yr incl. carbon</div>
          <HBars rows={alts.map((a) => ({ key: a.key, label: a.label, value: social[a.key] / 1e6, best: social[a.key] === minCost }))} unit="M" fmt={(v) => `$${v.toFixed(2)}`} />
        </div>
        <div>
          <div className="label mb-2">Carbon footprint · t CO₂ per year</div>
          <HBars rows={alts.map((a) => ({ key: a.key, label: a.label, value: a.co2_t_per_yr, best: a.co2_t_per_yr === minCo2 }))} unit=" t" fmt={(v) => num(v)} />
        </div>
      </div>

      <div className="label mt-5 mb-1.5">Who comes out ahead? 20-year NPV by party (vs keeping today's heating)</div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-ink-3 text-[10px]">
              <th className="text-left font-normal pb-1">Option</th>
              <th className="text-right font-normal">Served</th>
              {parties.map((p) => (
                <th key={p.id} className="text-right font-normal px-1">
                  <span className="inline-flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-sm" style={{ background: partyColor(p.id, parties) }} />{p.name.split(" (")[0]}</span>
                </th>
              ))}
              <th className="text-right font-normal">Everyone?</th>
            </tr>
          </thead>
          <tbody>
            {alts.map((a) => (
              <tr key={a.key} className={`border-t border-line ${a.key === "heatos" ? "bg-accent/5" : ""}`}>
                <td className="py-1.5 pr-2"><div className={a.key === "heatos" ? "text-ink font-semibold" : "text-ink-2"}>{a.label}</div><div className="text-[9.5px] text-ink-3">{a.description}</div></td>
                <td className="num text-right">{a.buildings}</td>
                {parties.map((p) => {
                  const v = a.party_npv_usd[p.id] ?? 0;
                  return <td key={p.id} className="num text-right px-1" style={{ color: v > 1000 ? "#4ade80" : v < -1000 ? "#ff6b6b" : "#a3adc2" }}>{Math.abs(v) < 1000 ? "≈0" : usd(v)}</td>;
                })}
                <td className="text-right text-[11px]" style={{ color: a.everyone_ahead ? "#4ade80" : "#fab219" }}>{a.everyone_ahead ? "✓" : `▲ ${parties.find((p) => p.id === a.min_party)?.name.split(" (")[0] ?? ""}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="label mt-5 mb-1">HeatOS plan: where the carbon goes (t CO₂ / yr, all {A.universe_buildings} buildings)</div>
      <Waterfall steps={heatos.carbon_waterfall} />
      <div className="text-[10.5px] text-ink-3 mt-2 space-y-0.5">
        {heatos.notes.concat(alts.find((a) => a.key === "standalone_ashp")?.notes.slice(1, 3) ?? []).map((n) => <div key={n}>· {n}</div>)}
      </div>
    </div>
  );
}

// ===================================================================== why? tree (graphical path)

function findNode(n: TreeNode, id: number): TreeNode | null {
  if (n.id === id) return n;
  return (n.yes && findNode(n.yes, id)) || (n.no && findNode(n.no, id)) || null;
}

export function WhyTreeDiagram({ ex, root }: { ex: Explanation; root: TreeNode }) {
  const steps = ex.path;
  return (
    <div className="relative">
      {steps.map((n, i) => {
        const node = findNode(root, n.id);
        const other = node && !node.leaf ? (n.answer === "yes" ? node.no : node.yes) : null;
        const last = i === steps.length - 1;
        return (
          <motion.div key={n.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.1 }} className="flex items-stretch gap-3">
            <div className="flex flex-col items-center w-[300px] shrink-0">
              <div className={`w-full rounded-lg px-3 py-2 border ${last ? "border-accent bg-accent/10" : "border-[#3987e588] bg-[#0f1628]"}`}>
                {last ? (
                  <>
                    <div className="label" style={{ color: "#ffb27a" }}>Decision</div>
                    <div className="text-[13.5px] font-semibold text-ink">{n.majority_label}</div>
                  </>
                ) : (
                  <>
                    <div className="text-[12px] text-ink">{n.question}</div>
                    <div className="text-[10.5px] num mt-0.5 text-ink-3">{n.value != null ? `this building: ${num(n.value, 1)}` : ""}</div>
                  </>
                )}
                <div className="flex items-center justify-between mt-1 text-[10px] num text-ink-3">
                  <span>{num(n.samples)} similar buildings</span><span>{Math.round(n.confidence * 100)}% confidence</span>
                </div>
              </div>
              {!last && (
                <div className="flex flex-col items-center py-1">
                  <div className="w-px h-3 bg-[#3987e5]" />
                  <span className="num text-[10px] px-1.5 rounded" style={{ color: n.answer === "yes" ? "#4ade80" : "#ec835a", background: "rgb(255 255 255 / 0.04)" }}>{n.answer}</span>
                  <div className="w-px h-3 bg-[#3987e5]" />
                </div>
              )}
            </div>
            {other && (
              <div className="flex items-start pt-3 gap-2 opacity-60">
                <div className="h-px w-6 mt-3 bg-line" />
                <div className="rounded-md px-2 py-1.5 border border-line text-[10.5px] max-w-[230px]">
                  <span className="num" style={{ color: n.answer === "yes" ? "#ec835a" : "#4ade80" }}>{n.answer === "yes" ? "no" : "yes"}</span>
                  <span className="text-ink-3"> → </span>
                  <span className="text-ink-2">{other.leaf ? other.majority_label : `mostly ${other.majority_label}`}</span>
                  <span className="num text-ink-3"> ({num(other.samples)})</span>
                </div>
              </div>
            )}
          </motion.div>
        );
      })}
      <div className="mt-2 flex items-center gap-2">
        <Chip color={ex.agrees ? "#4ade80" : "#fab219"}>{ex.agrees ? "tree agrees with the plan" : "tree differs from the plan"}</Chip>
        <span className="text-[10.5px] text-ink-3">Plan: {ex.recommendation_label}{ex.recommendation.connect ? ` · phase ${ex.recommendation.phase}` : ""}</span>
      </div>
      {ex.note && <div className="text-[11px] text-warning mt-1">{ex.note}</div>}
    </div>
  );
}
