import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useBundle, useStore } from "../store";
import { Chip } from "./bits";
import { num, partyColor, usd } from "../lib/format";
import type { Explanation, Physics, TransportOption, TreeNode } from "../frame";

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
        <Node x={352} y={121} title={bundle?.config.loop.delivery === "central_hot_water" ? "Hot-water network" : ambient ? "Ambient loop" : "Warm loop"} value={`${p.loop_supply_c}° → ${p.loop_return_c}°C`} sub={`${num(p.flow_m3h)} m³/h · pump ${num(p.pump_kw)} kW`} accent="#3987e5" />
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

// ===================================================================== team model

const fmtVal = (k: string, v: unknown): string => {
  if (typeof v !== "number") return String(v ?? "–");
  if (/usd/.test(k)) return usd(v);
  if (/utilization|share|pct/.test(k) && v <= 1.5) return `${Math.round(v * 100)}%`;
  return num(v, v < 10 ? 2 : 0);
};

export function TeamView() {
  const bundle = useBundle();
  const site = useStore((s) => s.site);
  const select = useStore((s) => s.select);
  const T = bundle?.team;
  if (!T) return <div className="text-[12px] text-ink-3">No team output found (run the team pipeline: <code>python optimize.py {site}</code>).</div>;
  const ourIds = new Set(bundle!.plan.items.filter((i) => i.connect).map((i) => i.building_id));
  const theirIds = new Set(T.chosen.map((c) => c.id));
  const both = [...ourIds].filter((x) => theirIds.has(x));
  const dcOurs = bundle!.config.data_center.capacity_mw_th;
  const dcTheirs = T.datacenter.heat_mw_th;
  const totals = T.totals ?? {};
  const keys = ["n_buildings", "pipe_m", "heat_delivered_gwh", "co2_avoided_t", "dc_utilization", "capex_usd", "annual_value_usd", "simple_payback_years"];
  const parties = T.ledger?.parties ?? [];
  const rob = T.robustness as Record<string, number[] | number> | undefined;
  return (
    <div className="space-y-4">
      <div className="text-[12px] text-ink-2">
        The team's parallel model (MILP plan on real LL84/PLUTO buildings, street-routed pipes, Monte Carlo, ledger), merged in.
        Toggle <b className="text-ink">Team plan routes</b> and <b className="text-ink">Infrastructure</b> in the legend to see it in 3D.
        <span className="ml-2">
          <a className="text-accent hover:underline" href={`/team/index.html${site === "lansing" ? "?site=lansing" : ""}`} target="_blank" rel="noreferrer">team map ↗</a>{" · "}
          <a className="text-accent hover:underline" href={`/team/proposal.html?site=${site}`} target="_blank" rel="noreferrer">proposal ↗</a>{" · "}
          <a className="text-accent hover:underline" href="/team/offer.html" target="_blank" rel="noreferrer">offers ↗</a>
        </span>
      </div>

      {dcTheirs != null && Math.abs(dcTheirs - dcOurs) > 0.5 && (
        <div className="p-3 rounded-lg text-[11.5px]" style={{ background: "rgb(250 178 25 / 0.08)", border: "1px solid #fab21955" }}>
          <b className="text-warning">▲ Assumption to reconcile:</b> data center heat is <b className="num">{dcOurs} MW</b> in the control room
          (site YAML, challenge working assumption) but <b className="num">{dcTheirs} MW</b> ({T.datacenter.usable_mw_th} MW usable) in the team model
          (derived from the building's LL84 electricity). This is why the two plans connect different buildings.
        </div>
      )}

      <div>
        <div className="label mb-1.5">Plans side by side</div>
        <div className="grid grid-cols-2 gap-3 text-[11px]">
          {[["Control room plan", [...ourIds]], ["Team MILP plan", [...theirIds]]].map(([title, ids]) => (
            <div key={title as string} className="p-2.5 rounded-lg border border-line">
              <div className="text-ink font-semibold mb-1">{title as string} · {(ids as string[]).length} buildings</div>
              {(ids as string[]).map((id) => {
                const b = bundle!.buildings.find((x) => x.id === id);
                return (
                  <button key={id} onClick={() => select(id)} className="block text-left w-full truncate hover:text-ink text-ink-2">
                    {both.includes(id) ? "● " : "○ "}{b?.name ?? id}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="text-[10.5px] text-ink-3 mt-1">● chosen by both · {both.length} in common</div>
      </div>

      <div>
        <div className="label mb-1.5">Team plan totals</div>
        <div className="grid grid-cols-4 gap-2 text-[11px]">
          {keys.filter((k) => totals[k] != null).map((k) => (
            <div key={k} className="p-2 rounded border border-line"><div className="text-ink-3 text-[10px]">{k.replace(/_/g, " ")}</div><div className="num text-ink">{fmtVal(k, totals[k])}</div></div>
          ))}
        </div>
      </div>

      {T.phases && T.phases.length > 0 && (
        <div>
          <div className="label mb-1.5">Phases</div>
          <table className="w-full text-[11px]">
            <thead><tr className="text-ink-3 text-[10px]"><th className="text-left font-normal">Phase</th><th className="text-right font-normal">Start</th><th className="text-right font-normal">Buildings</th><th className="text-right font-normal">Capex</th><th className="text-right font-normal">Heat GWh/yr</th><th className="text-right font-normal">CO₂ t/yr</th></tr></thead>
            <tbody>{T.phases.map((p, i) => (
              <tr key={i} className="border-t border-line"><td className="py-1">{String(p.label ?? p.phase)}</td><td className="num text-right">{String(p.start_year ?? "")}</td>
                <td className="num text-right">{String(p.n_buildings ?? "")}</td><td className="num text-right">{fmtVal("capex_usd", p.capex_usd)}</td>
                <td className="num text-right">{fmtVal("x", p.heat_delivered_gwh_year)}</td><td className="num text-right">{fmtVal("x", p.co2_avoided_t_year)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {parties.length > 0 && (
        <div>
          <div className="label mb-1.5">Team ledger · does everyone win? {T.ledger?.balanced ? "✓ balanced" : ""}</div>
          <table className="w-full text-[11px]">
            <thead><tr className="text-ink-3 text-[10px]"><th className="text-left font-normal">Party</th><th className="text-right font-normal">Net / yr</th><th className="text-right font-normal">NPV</th><th className="text-right font-normal">Wins in % of scenarios</th></tr></thead>
            <tbody>{parties.map((p, i) => (
              <tr key={i} className="border-t border-line"><td className="py-1">{String(p.party ?? p.id)}</td>
                <td className="num text-right">{fmtVal("usd", p.net_annual_usd)}</td><td className="num text-right">{fmtVal("usd", p.npv_usd)}</td>
                <td className="num text-right" style={{ color: Number(p.wins_in_pct_of_scenarios) >= (T.ledger?.threshold_pct ?? 80) ? "#4ade80" : "#fab219" }}>{p.wins_in_pct_of_scenarios != null ? `${num(Number(p.wins_in_pct_of_scenarios))}%` : "–"}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {rob && Array.isArray(rob.value_usd_p10_p50_p90) && (
        <div className="text-[11px]">
          <div className="label mb-1">Team Monte Carlo · {String(rob.n_runs ?? "")} runs (p10 / p50 / p90)</div>
          <div className="num text-ink-2">value {(rob.value_usd_p10_p50_p90 as number[]).map((v) => usd(v)).join(" / ")}
            {Array.isArray(rob.co2_avoided_t_p10_p50_p90) && <> · CO₂ {(rob.co2_avoided_t_p10_p50_p90 as number[]).map((v) => num(v)).join(" / ")} t</>}</div>
        </div>
      )}

      {T.match_scorecard && T.match_scorecard.length > 0 && (
        <div>
          <div className="label mb-1.5">Five-way match (team)</div>
          {T.match_scorecard.map((m) => <div key={m.axis} className="text-[11px] py-0.5"><b className="text-ink">{m.axis}:</b> <span className="text-ink-2">{m.headline}</span></div>)}
        </div>
      )}

      {T.infrastructure.length > 0 && (
        <div>
          <div className="label mb-1.5">Verified infrastructure nearby ({T.infrastructure.length})</div>
          <table className="w-full text-[11px]">
            <thead><tr className="text-ink-3 text-[10px]"><th className="text-left font-normal">Asset</th><th className="text-left font-normal">Type</th><th className="text-right font-normal">Distance</th><th className="text-left font-normal pl-2">Status</th><th className="text-left font-normal">Source</th></tr></thead>
            <tbody>{T.infrastructure.map((a) => (
              <tr key={a.id} className="border-t border-line" title={a.note}><td className="py-1 pr-2">{a.name}</td><td className="text-ink-2">{a.type.replace(/_/g, " ")}</td>
                <td className="num text-right">{a.distance_m != null ? `${num(a.distance_m)} m` : "–"}</td><td className="pl-2 text-ink-2">{a.status} · {a.confidence}</td>
                <td>{a.source ? <a className="text-accent hover:underline" href={a.source} target="_blank" rel="noreferrer">link</a> : ""}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {T.transport_methods.length > 0 && (
        <div>
          <div className="label mb-1.5">Heat transport methods considered</div>
          <table className="w-full text-[11px]">
            <thead><tr className="text-ink-3 text-[10px]"><th className="text-left font-normal">Method</th><th className="font-normal">Heat pump</th><th className="font-normal">New pipe</th><th className="font-normal">Existing network</th><th className="text-left font-normal">Distance sensitivity</th></tr></thead>
            <tbody>{T.transport_methods.map((m) => (
              <tr key={m.method_id} className="border-t border-line" title={m.description}><td className="py-1 pr-2">{m.method_name}</td>
                <td className="text-center text-ink-2">{m.requires_heat_pump}</td><td className="text-center text-ink-2">{m.requires_new_pipe}</td>
                <td className="text-center text-ink-2">{m.can_use_existing_network}</td><td className="text-ink-2">{m.distance_sensitivity}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ transport methods

const pctOrDash = (v: number | null) => (v == null ? "–" : `${Math.round(v * 100)}%`);

export function TransportView() {
  const bundle = useBundle();
  const T = bundle?.transport;
  const [obj, setObj] = useState<"cost-optimal" | "carbon-weighted">("cost-optimal");
  if (!T || !T.options) return <div className="text-[12px] text-ink-3">Run <code>make ml-models</code> then <code>.team-venv/bin/python transport_optimization.py</code> to compare transport methods.</div>;
  const rows = T.options.filter((r) => r.objective === obj);
  const live = rows.filter((r) => (r.connected ?? 0) > 0);
  const best = live.find((r) => r.option === T.best[obj]);
  const ranked = [...live].sort((a, b) => (b.system_net_musd_p50 ?? -1e9) - (a.system_net_musd_p50 ?? -1e9));
  const dead = rows.filter((r) => !r.connected);
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11.5px] text-ink-2">Design objective</div>
        <div className="flex rounded-lg bg-white/5 border border-line overflow-hidden">
          {(["cost-optimal", "carbon-weighted"] as const).map((o) => (
            <button key={o} onClick={() => setObj(o)} className={`px-2.5 py-1 text-[11px] ${obj === o ? "bg-white/12 text-ink" : "text-ink-3 hover:text-ink-2"}`}>
              {o === "cost-optimal" ? "Money only" : "Carbon at $190/t"}
            </button>
          ))}
        </div>
      </div>

      {best && (
        <div className="p-3 rounded-lg mb-4" style={{ background: "rgb(12 163 12 / 0.1)", border: "1px solid #0ca30c55" }}>
          <div className="label mb-1" style={{ color: "#4ade80" }}>Most efficient way to move the heat</div>
          <div className="text-[15px] font-semibold">{best.label}</div>
          <div className="text-[11.5px] text-ink-2 mt-1">{best.description}</div>
          <div className="grid grid-cols-4 gap-3 mt-3">
            <Stat label="Net value / yr" value={`$${(best.system_net_musd_p50 ?? 0).toFixed(1)}M`} />
            <Stat label="Cost of heat" value={`$${(best.lcoh_p50 ?? 0).toFixed(1)}`} sub={`vs $${(best.tariff_p50 ?? 0).toFixed(1)} tariff /MMBtu`} />
            <Stat label="CO₂ avoided / yr" value={`${num(best.net_co2_t_p50 ?? 0)} t`} />
            <Stat label="Everyone warm · all profit" value={`${pctOrDash(best.p_everyone_warm)} · ${pctOrDash(best.p_every_party_profits)}`} sub="of 1,000 futures" />
          </div>
        </div>
      )}

      <div className="label mb-2">Net value to the whole system · $M per year (median of 1,000 futures)</div>
      <HBars rows={ranked.map((r) => ({ key: r.option, label: r.label, value: r.system_net_musd_p50 ?? 0, best: r.option === best?.option }))} unit="M" fmt={(v) => `$${v.toFixed(1)}`} />

      <div className="label mt-5 mb-1.5">Every method, designed and stress-tested</div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-ink-3 text-[10px]">
              <th className="text-left font-normal pb-1">Method</th>
              <th className="text-right font-normal">Users</th>
              <th className="text-right font-normal">Pipe km</th>
              <th className="text-right font-normal">Capex</th>
              <th className="text-right font-normal">Heat cost / tariff</th>
              <th className="text-right font-normal">CO₂ t/yr</th>
              <th className="text-right font-normal">P(warm)</th>
              <th className="text-right font-normal">P(all profit)</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((r: TransportOption) => (
              <tr key={r.option} className="border-t border-line" style={r.option === best?.option ? { background: "rgb(12 163 12 / 0.08)" } : {}} title={r.description}>
                <td className="py-1.5 pr-2">{r.label}</td>
                <td className="num text-right">{num(r.connected ?? 0)}</td>
                <td className="num text-right">{(r.pipe_km ?? 0).toFixed(1)}</td>
                <td className="num text-right">${(r.capex_musd_p50 ?? 0).toFixed(1)}M</td>
                <td className="num text-right" style={{ color: (r.lcoh_p50 ?? 0) <= (r.tariff_p50 ?? 0) ? "#4ade80" : "#ec835a" }}>
                  ${(r.lcoh_p50 ?? 0).toFixed(0)} / ${(r.tariff_p50 ?? 0).toFixed(0)}
                </td>
                <td className="num text-right">{num(r.net_co2_t_p50 ?? 0)}</td>
                <td className="num text-right">{pctOrDash(r.p_everyone_warm)}</td>
                <td className="num text-right">{pctOrDash(r.p_every_party_profits)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {dead.length > 0 && (
        <div className="text-[11px] text-ink-3 mt-2">Not viable here (no building worth connecting): {dead.map((r) => r.label).join("; ")}.</div>
      )}
      {(T.companies?.length ?? 0) > 0 && (
        <>
          <div className="label mt-5 mb-1.5">Who could build and run it · third-party heat transport</div>
          <div className="grid grid-cols-2 gap-2">
            {T.companies!.map((c) => (
              <a key={c.company + c.role} href={c.source_url?.startsWith("http") ? c.source_url : undefined} target="_blank" rel="noreferrer"
                 className="block p-2.5 rounded-lg border border-line bg-white/5 hover:bg-white/10">
                <div className="flex items-center gap-2">
                  <span className="text-[12.5px] font-semibold">{c.company}</span>
                  <span className="ml-auto shrink-0"><Chip color="#86b6ef">{c.role_label}</Chip></span>
                </div>
                <div className="text-[11px] text-ink-2 mt-1 leading-snug">{c.assets.join(" · ")}</div>
                {c.note && <div className="text-[10.5px] text-ink-3 mt-1 leading-snug">{c.note}</div>}
                <div className="text-[10px] mt-1" style={{ color: "#c3b8ff" }}>
                  {c.distance_m ? `${num(c.distance_m)} m away · ` : ""}{c.status ? `${c.status} · ` : ""}fits: {c.fits_method}
                  {c.source_url?.startsWith("http") ? " · source ↗" : c.source_url ? ` · ${c.source_url}` : ""}
                </div>
              </a>
            ))}
          </div>
        </>
      )}
      <div className="text-[10.5px] text-ink-3 mt-3 leading-snug">
        Method: {T.method}. Heat cost = levelised cost of delivered heat; tariff = what buyers would pay (their current cost minus a discount).
        Source: the team's transport_optimization.py on the trained demand / supply models.
      </div>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="text-[10px] text-ink-3">{label}</div>
      <div className="num text-[15px]">{value}</div>
      {sub && <div className="text-[9.5px] text-ink-3">{sub}</div>}
    </div>
  );
}
