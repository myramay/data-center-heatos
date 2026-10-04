import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { useBundle, useStore } from "../store";
import { Bar, Chip, Gauge, Panel, Sparkline, VerdictBadge } from "./bits";
import { OPTION_LABEL, mw, num, pct, titleCase, usd } from "../lib/format";
import { API } from "../lib/data";
import type { Explanation } from "../frame";

type SortKey = "demand" | "distance" | "fuel" | "cost" | "equity" | "option" | "phase";
const SORTS: { id: SortKey; label: string }[] = [
  { id: "demand", label: "Demand" }, { id: "distance", label: "Distance" }, { id: "fuel", label: "Fuel" },
  { id: "cost", label: "Cost" }, { id: "equity", label: "Equity" }, { id: "option", label: "Option" }, { id: "phase", label: "Phase" },
];
const FUEL_COLOR: Record<string, string> = { steam: "#c3b8ff", gas_boiler: "#86b6ef", oil: "#ec835a", propane: "#fab219", electric: "#67e8f9", unknown: "#a3adc2" };

export function BuildingList() {
  const bundle = useBundle();
  const frames = useStore((s) => s.frames);
  const selected = useStore((s) => s.selected);
  const select = useStore((s) => s.select);
  const hover = useStore((s) => s.hover);
  const step = useStore((s) => s.step);
  const [sort, setSort] = useState<SortKey>("demand");
  const items = useMemo(() => new Map(bundle?.plan.items.map((i) => [i.building_id, i])), [bundle]);
  const history = useMemo(() => {
    const m = new Map<string, number[]>();
    for (const f of frames.slice(-72)) for (const b of f.buildings) {
      if (!m.has(b.id)) m.set(b.id, []);
      m.get(b.id)!.push(b.delivered_kw + b.unmet_kw);
    }
    return m;
  }, [frames]);
  const rows = useMemo(() => {
    const list = [...(bundle?.buildings ?? [])];
    const key: Record<SortKey, (b: (typeof list)[number]) => number | string> = {
      demand: (b) => -b.annual_heat_mwh, distance: (b) => b.street_distance_m, fuel: (b) => b.heating_system,
      cost: (b) => -b.current_heat_cost_usd_per_mwh, equity: (b) => -b.equity_score,
      option: (b) => (items.get(b.id)?.connect ? 0 : 1) + (items.get(b.id)?.option ?? ""),
      phase: (b) => items.get(b.id)?.phase ?? 9,
    };
    return list.sort((a, b) => (key[sort](a) < key[sort](b) ? -1 : key[sort](a) > key[sort](b) ? 1 : 0));
  }, [bundle, sort, items]);
  const nConnected = bundle?.plan.items.filter((i) => i.connect).length ?? 0;

  return (
    <Panel className="h-full"
           title={<span>Buildings <span className="num text-ink-2 normal-case tracking-normal">{bundle?.buildings.length ?? 0}</span></span>}
           right={step !== "analyze" && <span className="num text-[11px] text-ink-2">{nConnected} connected</span>}>
      <div className="flex flex-wrap gap-1 px-4 pb-2">
        {SORTS.map((s) => (
          <button key={s.id} onClick={() => setSort(s.id)}
                  className={`px-1.5 py-0.5 rounded text-[10.5px] ${sort === s.id ? "bg-white/12 text-ink" : "text-ink-3 hover:text-ink-2"}`}>{s.label}</button>
        ))}
      </div>
      <div className="overflow-y-auto scroll-thin px-2 pb-2 min-h-0 flex-1">
        {rows.map((b) => {
          const it = items.get(b.id);
          const on = step !== "analyze" && it?.connect;
          return (
            <button key={b.id} onClick={() => select(b.id === selected ? null : b.id)}
                    onMouseEnter={() => hover(b.id)} onMouseLeave={() => hover(null)}
                    className={`w-full text-left px-2 py-1.5 rounded-lg mb-0.5 transition-colors ${selected === b.id ? "bg-white/10" : "hover:bg-white/5"}`}>
              <div className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: on ? "#ff8a3d" : step === "analyze" ? "#475a7d" : "#2a3348" }} />
                <span className="text-[12px] text-ink truncate flex-1">{b.name}</span>
                <Sparkline values={history.get(b.id) ?? []} color={on ? "#ff8a3d" : "#5b6478"} width={46} height={14} />
              </div>
              <div className="flex items-center gap-1.5 mt-1 pl-3.5 min-w-0">
                <Chip color={FUEL_COLOR[b.heating_system]}>{b.heating_system.replace("_", " ")}</Chip>
                <span className="num text-[10px] text-ink-3 truncate min-w-0">{num(b.annual_heat_mwh)} MWh · {num(b.street_distance_m)} m · eq {b.equity_score.toFixed(2)}</span>
                {step !== "analyze" && it && (it.connect ? <span className="ml-auto"><Chip color="#ffb27a">P{it.phase} {OPTION_LABEL[it.option]}</Chip></span>
                  : <span className="ml-auto text-[10px] text-ink-3">{it.reason_codes[0] === "capacity_limit" ? "waitlist" : "—"}</span>)}
              </div>
            </button>
          );
        })}
      </div>
    </Panel>
  );
}

export function BuildingDetail({ id }: { id: string }) {
  const bundle = useBundle();
  const live = useStore((s) => s.live);
  const select = useStore((s) => s.select);
  const fb = useStore((s) => s.frame?.buildings.find((b) => b.id === id));
  const b = bundle?.buildings.find((x) => x.id === id);
  const [ex, setEx] = useState<Explanation | null>(bundle?.explanations[id] ?? null);
  useEffect(() => {
    setEx(bundle?.explanations[id] ?? null);
    if (live && !bundle?.explanations[id]) fetch(`${API}/explain/${id}`).then((r) => r.json()).then(setEx).catch(() => {});
  }, [id, bundle, live]);
  if (!b) return null;
  const rec = ex?.recommendation;
  const best = ex?.options.reduce((a, o) => (o.npv_value_usd > a.npv_value_usd ? o : a), ex.options[0]);
  return (
    <Panel className="h-full" title={b.id} right={<button onClick={() => select(null)} className="text-ink-3 hover:text-ink text-[13px]" aria-label="Close">✕</button>}>
      <div className="px-4 pb-4 overflow-y-auto scroll-thin min-h-0 flex-1">
        <div className="text-[16px] font-semibold leading-snug">{b.name}</div>
        <div className="text-[11.5px] text-ink-3 mt-0.5">{titleCase(b.use_type)} · built {b.year_built ?? "?"} · {b.is_estimated ? "estimated data" : "measured"}</div>
        <div className="grid grid-cols-3 gap-x-3 gap-y-2 mt-3 text-[11px]">
          {[["Heat", `${num(b.annual_heat_mwh)} MWh`], ["Now pays", `$${num(b.current_heat_cost_usd_per_mwh)}/MWh`], ["Fuel", b.heating_system.replace("_", " ")],
            ["Distance", `${num(b.street_distance_m)} m`], ["Needs", `${b.required_supply_temp_c}°C`], ["Equity", b.equity_score.toFixed(2)],
            ["Boiler", b.boiler_age_years != null ? `${b.boiler_age_years} yr` : "none"], ["Floor area", `${num(b.floor_area_m2)} m²`],
            ["Live", fb ? `${fb.mode} · ${mw(fb.delivered_kw)}` : "—"]].map(([k, v]) => (
            <div key={k}><div className="text-ink-3 text-[10px]">{k}</div><div className="num text-ink">{v}</div></div>
          ))}
        </div>

        {rec && (
          <div className="mt-4 p-3 rounded-lg" style={{ background: rec.connect ? "rgb(255 138 61 / 0.08)" : "rgb(148 163 184 / 0.06)", border: `1px solid ${rec.connect ? "#ff8a3d44" : "rgb(148 163 184 / 0.14)"}` }}>
            <div className="label">Recommendation</div>
            <div className="text-[15px] font-semibold mt-1" style={{ color: rec.connect ? "#ffb27a" : "#a3adc2" }}>
              {rec.connect ? `${ex?.recommendation_label} · phase ${rec.phase}` : "Not connected"}
            </div>
            {best && (
              <div className="grid grid-cols-2 gap-2 mt-2 text-[11px]">
                <div><div className="text-ink-3 text-[10px]">20-yr NPV incl. carbon</div><div className="num text-ink">{usd(best.npv_value_usd)}</div></div>
                <div><div className="text-ink-3 text-[10px]">Financial NPV only</div><div className="num text-ink">{usd(best.npv_financial_usd)}</div></div>
                <div><div className="text-ink-3 text-[10px]">CO₂ avoided / yr</div><div className="num text-ink">{num(best.annual_co2_avoided_t)} t</div></div>
                <div><div className="text-ink-3 text-[10px]">Capex + pipe</div><div className="num text-ink">{usd(best.capex_usd + best.pipe_usd)}</div></div>
              </div>
            )}
            {ex?.note && <div className="text-[11px] text-warning mt-2">{ex.note}</div>}
            <div className="flex flex-wrap gap-1 mt-2">
              {rec.reason_codes.filter((r) => !r.startsWith("best_option")).map((r) => <Chip key={r}>{r.replace(/_/g, " ")}</Chip>)}
            </div>
          </div>
        )}

        {ex && (
          <div className="mt-4">
            <div className="flex items-center justify-between">
              <div className="label">Why? decision path</div>
              <button onClick={() => useStore.getState().setView("tree")} className="text-[10.5px] text-accent hover:underline">open as tree ↗</button>
              <span className="text-[10px] text-ink-3">{ex.agrees ? "tree agrees with plan" : "tree differs from plan"}</span>
            </div>
            <div className="mt-2 relative">
              {ex.path.map((n, i) => (
                <motion.div key={n.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.12 }}
                            className="relative pl-6 pb-3">
                  <span className="absolute left-[7px] top-1 bottom-0 w-px bg-line" style={{ display: i === ex.path.length - 1 ? "none" : undefined }} />
                  <span className="absolute left-0.5 top-1 w-3 h-3 rounded-full border-2" style={{ borderColor: n.leaf ? "#ff8a3d" : "#3987e5", background: "#05070d" }} />
                  {n.leaf ? (
                    <div>
                      <div className="text-[12.5px] font-semibold text-ink">→ {n.majority_label}</div>
                      <div className="text-[10.5px] text-ink-3 num">{num(n.samples)} similar buildings</div>
                    </div>
                  ) : (
                    <div>
                      <div className="text-[12px] text-ink">{n.question}</div>
                      <div className="text-[11px] num mt-0.5">
                        <span style={{ color: n.answer === "yes" ? "#4ade80" : "#ec835a" }}>{n.answer}</span>
                        {n.value != null && <span className="text-ink-3"> · this building: {num(n.value, 1)}</span>}
                      </div>
                    </div>
                  )}
                  <div className="flex items-center gap-2 mt-1">
                    <div className="flex-1"><Bar value={n.confidence} color={n.leaf ? "#ff8a3d" : "#3987e5"} /></div>
                    <span className="num text-[10px] text-ink-3 w-16 text-right">{pct(n.confidence)} conf.</span>
                  </div>
                </motion.div>
              ))}
            </div>
            <div className="text-[10px] text-ink-3">Confidence = share of similar buildings with the same answer (Monte Carlo probability once the ML engine lands).</div>
          </div>
        )}

        {ex && (
          <div className="mt-4">
            <div className="label mb-1.5">Options compared</div>
            <table className="w-full text-[11px]">
              <thead><tr className="text-ink-3 text-[10px]"><th className="text-left font-normal">Option</th><th className="text-right font-normal">COP</th><th className="text-right font-normal">NPV + CO₂</th><th className="text-right font-normal">NPV</th></tr></thead>
              <tbody>
                {ex.options.map((o) => (
                  <tr key={o.option} className="border-t border-line">
                    <td className="py-1">{OPTION_LABEL[o.option]}</td>
                    <td className="num text-right">{o.cop ? o.cop.toFixed(1) : "direct"}</td>
                    <td className="num text-right" style={{ color: o.npv_value_usd > 0 ? "#4ade80" : "#ec835a" }}>{usd(o.npv_value_usd)}</td>
                    <td className="num text-right text-ink-2">{usd(o.npv_financial_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Panel>
  );
}

export function ConfidencePanel() {
  const f = useStore((s) => s.frame);
  const site = useStore((s) => s.site);
  const live = useStore((s) => s.live);
  const bundle = useBundle();
  const conf = f?.confidence ?? null;
  const active = f?.active_scenarios[0] ?? null;
  const [compare, setCompare] = useState<Record<string, number> | null>(null);
  useEffect(() => {
    const key = active ?? "baseline";
    const rec = bundle?.autopilot_compare?.[key];
    if (rec) { setCompare(rec.delta_mpc_minus_rules); return; }
    if (live) fetch(`${API}/compare?site=${site}${active ? `&scenario=${active}` : ""}`).then((r) => r.json())
      .then((d) => setCompare(d.delta_mpc_minus_rules)).catch(() => setCompare(null));
  }, [active, site, live, bundle]);

  return (
    <Panel className="h-full" title="Confidence" right={<VerdictBadge v={f?.verdict ?? null} />}>
      <div className="px-4 pb-4 overflow-y-auto scroll-thin min-h-0 flex-1">
        <div className="flex justify-center -mt-1">
          <Gauge value={conf?.p_all_warm ?? null} second={f?.jev?.p_supply_meets_guarantees ?? null}
                 label="P(everyone on network heat, next 48 h)" secondLabel="Jev" size={220} />
        </div>
        <div className="text-center text-[11px] text-ink-2 -mt-2 mb-3">P(every connected building stays on network heat, next 48 h)
          <div className="text-[10px] text-ink-3">arc: HeatOS Monte Carlo ({conf?.n_futures ?? "–"} futures) · <span style={{ color: "#c3b8ff" }}>needle: Jev {f?.jev ? `says ${Math.round(f.jev.p_supply_meets_guarantees * 100)}% (${Math.round(f.jev.latency_ms)} ms)` : "not answering: Monte Carlo only"}</span></div></div>
        <div className="grid grid-cols-3 gap-2 text-center -mt-1">
          <div><div className="label">Unmet h</div><div className="num text-[15px]">{conf ? conf.expected_unmet_hours.toFixed(1) : "–"}</div></div>
          <div><div className="label">Futures</div><div className="num text-[15px]">{conf?.n_futures ?? "–"}</div></div>
          <div><div className="label">Jev play</div><div className="text-[11px] text-ink-2 mt-1">{f?.jev ? `${f.jev.playbook.replace(/_/g, " ")} (${Math.round(f.jev.playbook_probability * 100)}%)` : "waiting / offline"}</div></div>
        </div>

        <div className="label mt-4 mb-1.5">Top uncertainty drivers</div>
        {(conf?.top_uncertainty_drivers ?? []).slice(0, 4).map((d) => (
          <div key={d.name} className="mb-1.5">
            <div className="flex justify-between text-[11px]"><span className="text-ink-2">{d.name}</span><span className="num text-ink-3">{pct(d.share)}</span></div>
            <Bar value={d.share} color="#3987e5" />
          </div>
        ))}

        <div className="label mt-4 mb-1.5">Margin</div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="num text-[17px]" style={{ color: (f?.margin.supply_margin_pct ?? 100) < 0 ? "#ff6b6b" : (f?.margin.supply_margin_pct ?? 100) < 25 ? "#fab219" : "#e8edf7" }}>
              {f ? `${f.margin.supply_margin_pct > 0 ? "+" : ""}${f.margin.supply_margin_pct.toFixed(0)}%` : "–"}
            </div>
            <div className="text-[10.5px] text-ink-3">heat available vs needed now</div>
          </div>
          <div>
            <div className="num text-[17px]">{f ? `${f.margin.storage_cover_h.toFixed(0)} h` : "–"}</div>
            <div className="text-[10.5px] text-ink-3">storage alone could carry the load</div>
          </div>
        </div>

        <div className="label mt-4 mb-1.5">Autopilot: MPC vs rules {active ? `· ${active.replace(/_/g, " ")}` : "· normal week"}</div>
        {compare ? (
          <div className="grid grid-cols-3 gap-2 text-[11px]">
            <div><div className="text-ink-3 text-[10px]">Cost incl. CO₂</div><div className="num" style={{ color: compare.cost_incl_carbon_usd <= 0 ? "#4ade80" : "#ec835a" }}>{usd(compare.cost_incl_carbon_usd)}</div></div>
            <div><div className="text-ink-3 text-[10px]">Backup hours</div><div className="num">{compare.hours_with_backup > 0 ? "+" : ""}{num(compare.hours_with_backup)}</div></div>
            <div><div className="text-ink-3 text-[10px]">CO₂</div><div className="num">{compare.network_emissions_t > 0 ? "+" : ""}{num(compare.network_emissions_t, 1)} t</div></div>
          </div>
        ) : <div className="text-[11px] text-ink-3">computing…</div>}
      </div>
    </Panel>
  );
}
