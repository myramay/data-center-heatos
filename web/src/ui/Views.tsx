import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { sankey, sankeyLinkHorizontal, type SankeyLink, type SankeyNode } from "d3-sankey";
import { marked } from "marked";
import { Bar as RBar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useBundle, useStore, type View } from "../store";
import { Bar, Chip, Panel, Sparkline } from "./bits";
import { num, partyColor, pct, usd } from "../lib/format";
import { API } from "../lib/data";
import { CompareView, PhysicsView, TeamView, TransportView, WhyTreeDiagram } from "./Views2";
import type { MoneyLink, TreeNode } from "../frame";

const NO_ROWS: never[] = [];
const TITLES: Record<Exclude<View, null>, string> = {
  transport: "How should the heat travel?",
  physics: "Network physics (live)", compare: "Is this the best deal for everyone?", team: "Team model (merged)", money: "Who pays whom", guarantees: "Heat guarantees", impact: "Sustainability impact",
  framework: "Decision framework: Chelsea vs Lansing", tree: "Why? decision tree", report: "Report: five deliverables",
};

export function Views() {
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  return (
    <AnimatePresence>
      {view && (
        <motion.div key={view} className="absolute right-[calc(var(--sw)+24px)] top-[76px] bottom-3 w-[min(780px,calc(100vw-var(--sw)-48px))] z-40"
                    initial={{ opacity: 0, x: 40 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 40 }} transition={{ type: "spring", damping: 26, stiffness: 260 }}>
          <Panel className="h-full" title={TITLES[view]}
                 right={<button onClick={() => setView(null)} className="text-ink-3 hover:text-ink text-[13px]" aria-label="Close">✕</button>}>
            <div className="px-4 pb-4 overflow-y-auto scroll-thin min-h-0 flex-1">
              {view === "transport" && <TransportView />}
              {view === "physics" && <PhysicsView />}
              {view === "compare" && <CompareView />}
              {view === "team" && <TeamView />}
              {view === "money" && <MoneyView />}
              {view === "guarantees" && <GuaranteesView />}
              {view === "impact" && <ImpactView />}
              {view === "framework" && <FrameworkView />}
              {view === "tree" && <TreeView />}
              {view === "report" && <ReportView />}
            </div>
          </Panel>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// ------------------------------------------------------------------ money

type SNode = SankeyNode<{ id: string; name: string }, object>;
type SLink = SankeyLink<{ id: string; name: string }, { label: string; value: number }>;

function Sankey({ nodes, links, parties }: { nodes: { id: string; name: string }[]; links: MoneyLink[]; parties: { id: string }[] }) {
  const [hover, setHover] = useState<string | null>(null);
  const W = 700, H = 300;
  const layout = useMemo(() => {
    const ids = new Set(nodes.map((n) => n.id));
    // collapse duplicate pairs and drop self/cyclic edges so d3-sankey gets a DAG
    const agg = new Map<string, { source: string; target: string; value: number; label: string }>();
    for (const l of links) {
      if (!ids.has(l.source) || !ids.has(l.target) || l.source === l.target || l.value_usd <= 0) continue;
      const k = `${l.source}>${l.target}`;
      const rev = agg.get(`${l.target}>${l.source}`);
      if (rev) { rev.value -= l.value_usd; continue; }
      const e = agg.get(k) ?? { source: l.source, target: l.target, value: 0, label: l.label };
      e.value += l.value_usd;
      if (!e.label.includes(l.label)) e.label += ` + ${l.label}`;
      agg.set(k, e);
    }
    const ls = [...agg.values()].map((e) => (e.value < 0 ? { ...e, source: e.target, target: e.source, value: -e.value } : e)).filter((e) => e.value > 1);
    if (!ls.length) return null;
    const used = new Set(ls.flatMap((l) => [l.source, l.target]));
    try {
      return sankey<{ id: string; name: string }, { label: string; value: number }>()
        .nodeId((d) => d.id).nodeWidth(10).nodePadding(14).extent([[4, 6], [W - 4, H - 6]])({
          nodes: nodes.filter((n) => used.has(n.id)).map((n) => ({ ...n })),
          links: ls.map((l) => ({ ...l })),
        });
    } catch {
      return null;
    }
  }, [nodes, links]);
  if (!layout) return <div className="text-[12px] text-ink-3 py-6">Money starts flowing once the network runs.</div>;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Money flows between parties">
      {layout.links.map((l, i) => {
        const s = l.source as SNode, t = l.target as SNode;
        const key = `${s.id}>${t.id}`;
        return (
          <motion.path key={key} d={sankeyLinkHorizontal()(l as never) ?? ""} fill="none" stroke={partyColor(s.id, parties)}
                       strokeWidth={Math.max(1, l.width ?? 1)} initial={false} animate={{ d: sankeyLinkHorizontal()(l as never) ?? "", strokeWidth: Math.max(1, l.width ?? 1) }}
                       strokeOpacity={hover && hover !== key ? 0.12 : 0.42} onMouseEnter={() => setHover(key)} onMouseLeave={() => setHover(null)}>
            <title>{`${s.name} → ${t.name}: ${(l as SLink).label} · ${usd(l.value)}`}</title>
          </motion.path>
        );
        void i;
      })}
      {layout.nodes.map((n) => (
        <g key={n.id}>
          <rect x={n.x0} y={n.y0} width={(n.x1 ?? 0) - (n.x0 ?? 0)} height={Math.max(1, (n.y1 ?? 0) - (n.y0 ?? 0))} rx={2} fill={partyColor(n.id, parties)} />
          <text x={(n.x0 ?? 0) < W / 2 ? (n.x1 ?? 0) + 6 : (n.x0 ?? 0) - 6} y={((n.y0 ?? 0) + (n.y1 ?? 0)) / 2} dy="0.35em"
                textAnchor={(n.x0 ?? 0) < W / 2 ? "start" : "end"} fill="#e8edf7" fontSize={11}>
            {n.name} <tspan fill="#6b7690" className="num">{usd(n.value ?? 0)}</tspan>
          </text>
        </g>
      ))}
    </svg>
  );
}

function MoneyView() {
  const bundle = useBundle();
  const f = useStore((s) => s.frame);
  const [range, setRange] = useState<"24h" | "year">("24h");
  if (!bundle) return null;
  const parties = bundle.config.parties;
  const sk = range === "24h" ? f?.money.sankey_24h : (bundle.annual as unknown as { sankey: { nodes: { id: string; name: string }[]; links: MoneyLink[] } }).sankey;
  const deal = bundle.deal;
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="text-[12px] text-ink-2">Money flows {range === "24h" ? "over the last 24 simulated hours" : "over a full simulated year"}</div>
        <div className="flex rounded-md bg-white/5 border border-line overflow-hidden">
          {(["24h", "year"] as const).map((r) => (
            <button key={r} onClick={() => setRange(r)} className={`px-2 py-1 text-[11px] ${range === r ? "bg-white/12 text-ink" : "text-ink-3"}`}>{r === "24h" ? "Last 24 h" : "Annual"}</button>
          ))}
        </div>
      </div>
      {sk && <Sankey nodes={sk.nodes} links={sk.links} parties={parties} />}

      <div className="mt-3 p-3 rounded-lg" style={{ background: deal.cleared ? "rgb(12 163 12 / 0.1)" : "rgb(250 178 25 / 0.08)", border: `1px solid ${deal.cleared ? "#0ca30c55" : "#fab21955"}` }}>
        <div className="flex items-center gap-2">
          <span className="text-[14px]" aria-hidden>{deal.cleared ? "✓" : "▲"}</span>
          <span className="text-[14px] font-semibold">Everyone wins? {deal.cleared ? "Yes" : "Not yet"}</span>
          <span className="text-[11px] text-ink-3 ml-auto num">{deal.n_futures} futures · {deal.candidates_tried} term sets tried</span>
        </div>
        <div className="text-[12px] text-ink-2 mt-1">
          {deal.cleared ? "Every party comes out ahead in at least 80% of sampled futures" : `Below 80%: ${deal.binding_parties.map((p) => parties.find((x) => x.id === p)?.name ?? p).join(", ")}`}
          {deal.changes.length > 0 && <> — after: {deal.changes.join("; ")}</>}.
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 mt-3">
        {bundle.annual.parties.map((p) => {
          const ok = (p.p_ahead ?? 0) >= 0.8;
          return (
            <div key={p.id} className="p-3 rounded-lg border border-line bg-white/[0.02]">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-sm" style={{ background: partyColor(p.id, parties) }} />
                <span className="text-[12.5px] font-semibold truncate">{p.name}</span>
                <span className="ml-auto text-[11px] font-semibold" style={{ color: ok ? "#4ade80" : "#fab219" }}>{ok ? "✓" : "▲"} {pct(p.p_ahead)}</span>
              </div>
              <div className="grid grid-cols-3 gap-1 mt-2 text-[10.5px]">
                <div><div className="text-ink-3">20-yr NPV</div><div className="num text-ink">{usd(p.npv_usd)}</div></div>
                <div><div className="text-ink-3">Payback</div><div className="num text-ink">{p.payback_year == null ? "—" : p.payback_year === 0 ? "day 1" : `yr ${p.payback_year}`}</div></div>
                <div><div className="text-ink-3">This run</div><div className="num text-ink">{usd(f?.money.party_totals[p.id] ?? 0)}</div></div>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <Sparkline values={p.cumulative.map((v) => v - Math.min(0, ...p.cumulative))} band={0} width={150} height={22} color={partyColor(p.id, parties)} />
                <span className="text-[9.5px] text-ink-3">cumulative cash, {p.horizon_years} yr</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ guarantees

function GuaranteesView() {
  const rows = useStore((s) => s.frame?.guarantees) ?? NO_ROWS;
  const bundle = useBundle();
  return (
    <div>
      <div className="text-[12px] text-ink-2 mb-3">
        Guaranteed buildings are served first. If the network misses an hour, the owner refunds 3× that hour's heat bill.
        Premium = expected refunds + CVaR95 risk margin over sampled futures.
      </div>
      <table className="w-full text-[12px]">
        <thead><tr className="text-ink-3 text-[10.5px]">
          <th className="text-left font-normal pb-1">Building</th><th className="text-right font-normal">Premium / yr</th>
          <th className="font-normal w-40">P(promise kept, 48 h)</th><th className="text-right font-normal">Missed h</th><th className="text-right font-normal">Refunds owed</th>
        </tr></thead>
        <tbody>
          {rows.map((g) => (
            <tr key={g.building_id} className="border-t border-line">
              <td className="py-2">
                <div className="text-ink">{g.name}</div>
                <div className="text-[10.5px]" style={{ color: g.on_backup ? "#ff6b6b" : "#4ade80" }}>{g.on_backup ? "▲ on backup now" : "✓ on network heat"}</div>
              </td>
              <td className="num text-right">{usd(g.premium_usd_per_yr)}</td>
              <td className="px-3"><div className="flex items-center gap-2"><Bar value={g.p_kept ?? 0} color={(g.p_kept ?? 0) >= 0.9 ? "#0ca30c" : "#fab219"} /><span className="num text-[11px] w-10">{pct(g.p_kept)}</span></div></td>
              <td className="num text-right">{g.missed_hours}</td>
              <td className="num text-right">{usd(g.refunds_owed_usd, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <div className="text-[12px] text-ink-3 mt-3">No guaranteed buildings are connected in this plan.</div>}
      <div className="text-[11px] text-ink-3 mt-4">Annual premiums from the deal check ({bundle?.deal.n_futures} full-year futures). A $0 premium means no misses in any sampled future.</div>
    </div>
  );
}

// ------------------------------------------------------------------ impact

function Counter({ value, digits = 0, unit, label }: { value: number; digits?: number; unit: string; label: string }) {
  return (
    <div className="p-3 rounded-lg border border-line bg-white/[0.02]">
      <div className="label">{label}</div>
      <div className="num text-[22px] font-semibold mt-1">{num(value, digits)} <span className="text-[12px] text-ink-3 font-normal">{unit}</span></div>
    </div>
  );
}

function ImpactView() {
  const bundle = useBundle();
  const run = useStore((s) => s.frame?.impact_running);
  if (!bundle) return null;
  const im = bundle.annual.impact;
  return (
    <div>
      <div className="label mb-2">This run so far</div>
      <div className="grid grid-cols-4 gap-2">
        <Counter label="Heat delivered" value={run?.heat_mwh ?? 0} unit="MWh" />
        <Counter label="Net CO₂ avoided" value={run?.co2_t ?? 0} digits={1} unit="t" />
        <Counter label="Net water" value={run?.water_m3 ?? 0} unit="m³" />
        <Counter label="Backup heat" value={run?.backup_mwh ?? 0} digits={1} unit="MWh" />
      </div>
      <div className="label mt-4 mb-2">Full simulated year</div>
      <div className="grid grid-cols-4 gap-2 text-[12px]">
        <div><div className="text-ink-3 text-[10.5px]">Net CO₂ avoided</div><div className="num text-[16px]">{num(im.net_co2_avoided_t.central)} t</div>
          <div className="num text-[10.5px] text-ink-3">range {num(im.net_co2_avoided_t.low)}–{num(im.net_co2_avoided_t.high)}</div></div>
        <div><div className="text-ink-3 text-[10.5px]">Per MW thermal-yr</div><div className="num text-[16px]">{num(im.net_co2_t_per_mw_yr.central)} t</div></div>
        <div><div className="text-ink-3 text-[10.5px]">ERF / ERE</div><div className="num text-[16px]">{im.erf.toFixed(2)} / {im.ere.toFixed(2)}</div></div>
        <div><div className="text-ink-3 text-[10.5px]">Net water / yr</div><div className="num text-[16px]">{im.water ? `${num(im.water.net_water_saved_m3)} m³` : "n/a"}</div>
          {im.water && im.water.net_water_saved_m3 < 0 && <div className="text-[10.5px] text-warning">heat pump power outweighs tower savings</div>}</div>
      </div>
      <div className="label mt-4">HDR regenerative scorecard (−2 … +2)</div>
      <ScoreRadar rows={im.scorecard} />
      <table className="w-full text-[11px] mt-1">
        <tbody>
          {im.scorecard.map((r) => (
            <tr key={r.category} className="border-t border-line">
              <td className="py-1 pr-2 text-ink w-28">{r.category}</td>
              <td className="num pr-2 text-ink-3 w-16">{r.baseline} → <span className="text-ink">{r.with_heatos}</span></td>
              <td className="text-ink-2">{r.change_basis}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScoreRadar({ rows }: { rows: { category: string; baseline: number; with_heatos: number; change_basis: string }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 420, H = 270, cx = W / 2, cy = H / 2 + 4, R = 100;
  const n = rows.length;
  const pt = (i: number, v: number) => {
    const a = -Math.PI / 2 + (i / n) * Math.PI * 2;
    const r = ((v + 2) / 4) * R;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)] as const;
  };
  const poly = (key: "baseline" | "with_heatos") => rows.map((r, i) => pt(i, r[key]).join(",")).join(" ");
  return (
    <div className="flex items-center gap-4">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-[420px] max-w-full" role="img" aria-label="HDR scorecard baseline vs with HeatOS">
        {[-2, -1, 0, 1, 2].map((v) => (
          <polygon key={v} points={rows.map((_, i) => pt(i, v).join(",")).join(" ")} fill="none"
                   stroke={v === 0 ? "rgb(148 163 184 / 0.35)" : "rgb(148 163 184 / 0.13)"} strokeDasharray={v === 0 ? "3 3" : undefined} />
        ))}
        {rows.map((r, i) => {
          const [x, y] = pt(i, 2);
          const [lx, ly] = pt(i, 2.75);
          return (
            <g key={r.category} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <line x1={cx} y1={cy} x2={x} y2={y} stroke="rgb(148 163 184 / 0.13)" />
              <text x={lx} y={ly} textAnchor={Math.abs(lx - cx) < 8 ? "middle" : lx > cx ? "start" : "end"} dy="0.35em"
                    fill={hover === i ? "#e8edf7" : "#a3adc2"} fontSize={11}>{r.category}</text>
            </g>
          );
        })}
        {[-2, 0, 2].map((v) => { const [x, y] = pt(0, v); return <text key={v} x={x + 5} y={y} fill="#6b7690" fontSize={9} className="num">{v > 0 ? `+${v}` : v}</text>; })}
        <polygon points={poly("baseline")} fill="#8a94ab" fillOpacity={0.12} stroke="#8a94ab" strokeWidth={2} />
        <motion.polygon points={poly("with_heatos")} fill="#3987e5" fillOpacity={0.25} stroke="#3987e5" strokeWidth={2}
                        initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} style={{ transformOrigin: `${cx}px ${cy}px` }} />
        {rows.map((r, i) => { const [x, y] = pt(i, r.with_heatos); return <circle key={i} cx={x} cy={y} r={4} fill="#3987e5" stroke="#0b1020" strokeWidth={2} />; })}
      </svg>
      <div className="text-[11px] min-w-0">
        <div className="flex items-center gap-3 mb-2">
          <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-[#8a94ab]" />Baseline</span>
          <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-[#3987e5]" />With HeatOS</span>
        </div>
        <div className="text-ink-3">{hover != null ? rows[hover].change_basis : "Hover a category for the basis of its score."}</div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ framework

function FrameworkView() {
  const bundle = useBundle();
  if (!bundle) return null;
  const sc = bundle.site_scores;
  const h = sc.robustness.difference_histogram;
  const data = h.counts.map((c, i) => ({ x: ((h.edges[i] + h.edges[i + 1]) / 2).toFixed(2), count: c, mid: (h.edges[i] + h.edges[i + 1]) / 2 }));
  return (
    <div>
      <table className="w-full text-[12px]">
        <thead><tr className="text-ink-3 text-[10.5px]"><th className="text-left font-normal pb-1">Criterion</th><th className="font-normal w-28">Weight</th><th className="text-right font-normal">Chelsea</th><th className="text-right font-normal">Lansing</th></tr></thead>
        <tbody>
          {sc.criteria.map((c) => (
            <tr key={c.key} className="border-t border-line" title={c.rationale}>
              <td className="py-1.5"><div className="text-ink">{c.label}</div><div className="text-[10.5px] text-ink-3">{c.rationale}</div></td>
              <td className="px-2"><div className="flex items-center gap-2"><Bar value={c.weight / 0.15} color="#8a94ab" /><span className="num text-[11px]">{Math.round(c.weight * 100)}%</span></div></td>
              <td className="num text-right" style={{ color: c.chelsea > c.lansing ? "#e8edf7" : "#6b7690" }}>{c.chelsea}</td>
              <td className="num text-right" style={{ color: c.lansing > c.chelsea ? "#e8edf7" : "#6b7690" }}>{c.lansing}</td>
            </tr>
          ))}
          <tr className="border-t border-line font-semibold">
            <td className="py-2">Weighted total</td><td />
            <td className="num text-right text-[15px]">{sc.totals.chelsea.toFixed(2)}</td>
            <td className="num text-right text-[15px] text-ink-2">{sc.totals.lansing.toFixed(2)}</td>
          </tr>
        </tbody>
      </table>
      <div className="mt-4 flex items-baseline gap-3">
        <div className="num text-[28px] font-semibold">{pct(sc.robustness.p_chelsea_wins, 1)}</div>
        <div className="text-[12px] text-ink-2">of {num(sc.robustness.n_draws)} random re-weightings (Dirichlet α = w×40, ±0.75 score noise) rank Chelsea first</div>
      </div>
      <div className="label mt-3">Chelsea score − Lansing score across draws</div>
      <div className="h-[170px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -18 }} barCategoryGap={2}>
            <CartesianGrid stroke="rgb(148 163 184 / 0.08)" vertical={false} />
            <XAxis dataKey="x" tick={{ fill: "#6b7690", fontSize: 10 }} tickLine={false} interval={7} />
            <YAxis tick={{ fill: "#6b7690", fontSize: 10 }} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={{ background: "#0b1020", border: "1px solid rgb(148 163 184 / 0.2)", borderRadius: 8, fontSize: 11 }} formatter={(v) => [`${num(Number(v))} draws`, "count"]} labelFormatter={(l) => `difference ${l}`} />
            <ReferenceLine x={data.reduce((a, d) => (Math.abs(d.mid) < Math.abs(a.mid) ? d : a), data[0])?.x} stroke="#e8edf7" strokeDasharray="3 3" label={{ value: "tie", fill: "#a3adc2", fontSize: 10, position: "top" }} />
            <RBar dataKey="count" fill="#3987e5" radius={[3, 3, 0, 0]} isAnimationActive />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ tree

function TreeBranch({ node, depth, highlight, edge }: { node: TreeNode; depth: number; highlight: Set<number>; edge?: "yes" | "no" }) {
  const [open, setOpen] = useState(depth < 2 || highlight.has(node.id));
  useEffect(() => { if (highlight.has(node.id)) setOpen(true); }, [highlight, node.id]);
  const on = highlight.has(node.id);
  return (
    <div className="relative" style={{ marginLeft: depth ? 16 : 0 }}>
      <button onClick={() => setOpen(!open)} className={`w-full text-left flex items-center gap-2 py-1 px-2 rounded-md ${on ? "bg-accent/10" : "hover:bg-white/5"}`}>
        {edge && <span className="num text-[10px] w-6" style={{ color: edge === "yes" ? "#4ade80" : "#ec835a" }}>{edge}</span>}
        {!node.leaf && <span className="text-ink-3 text-[10px] w-3">{open ? "▾" : "▸"}</span>}
        <span className={`text-[12px] ${node.leaf ? "font-semibold" : ""}`} style={{ color: on ? "#ffb27a" : node.leaf ? "#e8edf7" : "#a3adc2" }}>
          {node.leaf ? `→ ${node.majority_label}` : node.question}
        </span>
        <span className="ml-auto flex items-center gap-2 shrink-0">
          <span className="num text-[10px] text-ink-3">{num(node.samples)}</span>
          <span className="w-14"><Bar value={node.purity} color={node.leaf ? "#ff8a3d" : "#3987e5"} height={3} /></span>
        </span>
      </button>
      {open && !node.leaf && node.yes && node.no && (
        <div className="border-l border-line ml-3">
          <TreeBranch node={node.yes} depth={depth + 1} highlight={highlight} edge="yes" />
          <TreeBranch node={node.no} depth={depth + 1} highlight={highlight} edge="no" />
        </div>
      )}
    </div>
  );
}

function TreeView() {
  const bundle = useBundle();
  const selected = useStore((s) => s.selected);
  const select = useStore((s) => s.select);
  if (!bundle) return null;
  const path = new Set(selected ? bundle.explanations[selected]?.path.map((n) => n.id) ?? [] : []);
  const ex = selected ? bundle.explanations[selected] : null;
  const name = (id: string) => bundle.buildings.find((b) => b.id === id)?.name ?? id;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5 mb-3">
        <span className="label mr-1">Building</span>
        {bundle.buildings.slice().sort((a, b) => Number(!!bundle.plan.items.find((i) => i.building_id === b.id)?.connect) < Number(!!bundle.plan.items.find((i) => i.building_id === a.id)?.connect) ? -1 : 1)
          .slice(0, 14).map((b) => (
          <button key={b.id} onClick={() => select(b.id)} className={`px-2 py-0.5 rounded text-[10.5px] border ${selected === b.id ? "border-accent text-ink bg-accent/10" : "border-line text-ink-3 hover:text-ink-2"}`}>{b.name.split(",")[0]}</button>
        ))}
      </div>
      {ex ? (
        <div className="mb-5">
          <div className="text-[13px] font-semibold mb-2">Why {name(ex.building_id)}?</div>
          <WhyTreeDiagram ex={ex} root={bundle.tree.root} />
        </div>
      ) : <div className="text-[12px] text-ink-3 mb-4">Pick a building above, or click one in the 3D view, to see its path through the tree.</div>}
      <div className="label mb-1">Full tree</div>
      <div className="text-[12px] text-ink-2 mb-2">
        Trained on {num(bundle.tree.n_samples)} synthetic building variants labelled by the recommendation engine
        (agreement {pct(bundle.tree.train_accuracy)}). {selected ? <>Highlighted: path for <b className="text-ink">{selected}</b>.</> : "Select a building to highlight its path."}
      </div>
      <div className="flex flex-wrap gap-1 mb-2">
        {bundle.tree.classes.map((c) => <Chip key={c}>{bundle.tree.labels[c] ?? c}</Chip>)}
      </div>
      <TreeBranch node={bundle.tree.root} depth={0} highlight={path} />
    </div>
  );
}

// ------------------------------------------------------------------ report

function ReportView() {
  const bundle = useBundle();
  const live = useStore((s) => s.live);
  const runId = useStore((s) => s.session?.runId ?? null);
  const site = useStore((s) => s.site);
  const [md, setMd] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const generate = async () => {
    setBusy(true);
    try {
      if (live && runId) setMd(await fetch(`${API}/report?run_id=${runId}&format=md`).then((r) => r.text()));
      else setMd(bundle?.report_md ?? "No recorded report available.");
    } finally { setBusy(false); }
  };
  useEffect(() => { void generate(); }, [site]); // eslint-disable-line react-hooks/exhaustive-deps
  const html = useMemo(() => (md ? (marked.parse(md) as string) : ""), [md]);
  const download = () => {
    const blob = new Blob([md ?? ""], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `heatos_${site}_report.md`;
    a.click();
  };
  return (
    <div>
      <div className="flex items-center gap-2 mb-3 sticky top-0 py-1 z-10" style={{ background: "rgb(11 16 32 / 0.9)" }}>
        {live && (
          <button onClick={generate} disabled={busy} className="px-3 py-1.5 rounded-lg text-[12px] font-semibold text-void" style={{ background: "#ff8a3d" }}>
            {busy ? "Generating…" : "Regenerate from live model"}
          </button>
        )}
        {live && runId
          ? <a href={`${API}/report?run_id=${runId}&format=pdf`} className="px-3 py-1.5 rounded-lg text-[12px] border border-line bg-white/5 hover:bg-white/10">Download PDF</a>
          : <button onClick={() => window.print()} className="px-3 py-1.5 rounded-lg text-[12px] border border-line bg-white/5 hover:bg-white/10">Print / save PDF</button>}
        <button onClick={download} className="px-3 py-1.5 rounded-lg text-[12px] border border-line bg-white/5 hover:bg-white/10">Markdown</button>
        <span className="text-[10.5px] text-ink-3 ml-auto">{live ? "live engine" : "recorded run"}</span>
      </div>
      <div className="report" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
