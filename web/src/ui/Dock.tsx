import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useBundle, useStore } from "../store";
import { Panel } from "./bits";

const NONE: string[] = [];
const SEV_COLOR = { info: "#86b6ef", warn: "#fab219", alert: "#ff6b6b", ok: "#4ade80" } as const;
const SEV_ICON = { info: "•", warn: "▲", alert: "■", ok: "✓" } as const;
const SCENARIO_ICON: Record<string, string> = {
  polar_vortex: "❄", lake_effect_cold_snap: "❄", tenant_leaves: "⇣", server_outage: "⏻", heat_wave: "☀",
  price_spike: "$", greenhouse_off_season: "❀", bitcoin_price_crash: "₿",
};

export function StressToggles() {
  const bundle = useBundle();
  const fire = useStore((s) => s.fireScenario);
  const active = useStore((s) => s.frame?.active_scenarios) ?? NONE;
  const [pressed, setPressed] = useState<string | null>(null);
  return (
    <Panel title="Stress-test the network">
      <div className="grid grid-cols-2 gap-2 px-3 pb-3">
        {bundle?.scenarios.map((sc, i) => {
          const on = active.includes(sc.name);
          return (
            <motion.button key={sc.name} whileTap={{ scale: 0.94, y: 2 }}
                           onClick={() => { setPressed(sc.name); setTimeout(() => setPressed(null), 500); void fire(sc.name); }}
                           className="relative text-left px-2.5 py-2 rounded-lg border overflow-hidden"
                           style={{
                             borderColor: on ? "#ff8a3d88" : "rgb(148 163 184 / 0.18)",
                             background: on ? "linear-gradient(180deg, rgb(255 138 61 / 0.22), rgb(255 138 61 / 0.06))" : "linear-gradient(180deg, rgb(255 255 255 / 0.06), rgb(255 255 255 / 0.015))",
                             boxShadow: on ? "0 0 18px rgb(255 138 61 / 0.25), inset 0 -2px 0 rgb(0 0 0 / 0.4)" : "inset 0 -2px 0 rgb(0 0 0 / 0.45), inset 0 1px 0 rgb(255 255 255 / 0.06)",
                           }}>
              {pressed === sc.name && <motion.span className="absolute inset-0 bg-white/20" initial={{ opacity: 0.8 }} animate={{ opacity: 0 }} transition={{ duration: 0.5 }} />}
              <div className="flex items-center gap-1.5">
                <span className="text-[13px]" aria-hidden>{SCENARIO_ICON[sc.name] ?? "!"}</span>
                <span className="text-[11.5px] font-medium leading-tight">{sc.label}</span>
                <span className={`ml-auto w-1.5 h-1.5 rounded-full ${on ? "bg-accent" : "bg-white/15"}`} style={on ? { boxShadow: "0 0 8px #ff8a3d" } : {}} />
              </div>
              <div className="num text-[9.5px] text-ink-3 mt-0.5">{sc.hours ? `${sc.hours} h` : "rest of run"} · key {["Q", "W", "E", "R", "T"][i] ?? ""}</div>
            </motion.button>
          );
        })}
      </div>
    </Panel>
  );
}

export function EventTicker() {
  const events = useStore((s) => s.events);
  const list = [...events].reverse().slice(0, 30);
  return (
    <Panel title="Event log" className="h-full">
      <div className="overflow-y-auto scroll-thin px-3 pb-3 min-h-0 flex-1">
        <AnimatePresence initial={false}>
          {list.length === 0 && <div className="text-[11.5px] text-ink-3 px-1">Waiting for the network to start…</div>}
          {list.map((e) => (
            <motion.div key={`${e.hour_index}-${e.text}`} layout initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
                        className="flex gap-2 py-1 border-b border-line last:border-0">
              <span className="num text-[10px] text-ink-3 w-[52px] shrink-0 pt-px">
                {new Date(e.time).toLocaleDateString("en-US", { weekday: "short" })} {String(new Date(e.time).getHours()).padStart(2, "0")}:00
              </span>
              <span className="text-[11px] shrink-0" style={{ color: SEV_COLOR[e.severity] }} aria-label={e.severity}>{SEV_ICON[e.severity]}</span>
              <span className="text-[11.5px] text-ink-2 leading-snug">{e.text}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </Panel>
  );
}

export function FlowChart() {
  const frames = useStore((s) => s.frames);
  const data = useMemo(() => frames.slice(-72).map((f) => {
    const storage = f.storage.reduce((a, s) => a + s.out_kw, 0) / 1000;
    const network = Math.max(f.network_kw / 1000 - storage, 0);
    return {
      t: (() => { const d = new Date(f.time); return `${d.toLocaleDateString("en-US", { weekday: "short" })} ${String(d.getHours()).padStart(2, "0")}h`; })(),
      network: +network.toFixed(2), storage: +storage.toFixed(2), backup: +(f.backup_kw / 1000).toFixed(2),
      demand: +(f.demand_kw / 1000).toFixed(2), supply: +(f.data_center.offered_kw / 1000).toFixed(2),
    };
  }), [frames]);
  return (
    <Panel title="Heat supply vs demand · last 72 h (MW)" className="h-[230px]">
      <div className="flex-1 min-h-0 px-2 pb-2">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="rgb(148 163 184 / 0.08)" vertical={false} />
            <XAxis dataKey="t" tick={{ fill: "#6b7690", fontSize: 10 }} tickLine={false} axisLine={{ stroke: "rgb(148 163 184 / 0.2)" }} interval={11} minTickGap={20} />
            <YAxis tick={{ fill: "#6b7690", fontSize: 10, fontFamily: "JetBrains Mono" }} tickLine={false} axisLine={false} width={42} />
            <Tooltip contentStyle={{ background: "#0b1020", border: "1px solid rgb(148 163 184 / 0.2)", borderRadius: 8, fontSize: 11 }}
                     labelStyle={{ color: "#a3adc2" }} itemStyle={{ color: "#e8edf7" }} formatter={(v) => `${Number(v).toFixed(2)} MW`} />
            <Legend iconType="circle" iconSize={7} wrapperStyle={{ fontSize: 10.5, color: "#a3adc2", paddingTop: 0 }} verticalAlign="top" height={20} />
            <Area type="monotone" dataKey="network" name="Data center heat" stackId="1" stroke="#3987e5" fill="#3987e5" fillOpacity={0.55} strokeWidth={1.5} isAnimationActive={false} />
            <Area type="monotone" dataKey="storage" name="Storage" stackId="1" stroke="#199e70" fill="#199e70" fillOpacity={0.6} strokeWidth={1.5} isAnimationActive={false} />
            <Area type="monotone" dataKey="backup" name="Existing boilers" stackId="1" stroke="#d95926" fill="#d95926" fillOpacity={0.6} strokeWidth={1.5} isAnimationActive={false} />
            <Line type="monotone" dataKey="demand" name="Demand" stroke="#e8edf7" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="supply" name="Data center offers" stroke="#a3adc2" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}
