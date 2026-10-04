import { motion } from "framer-motion";
import { STEPS, useStore, type View } from "../store";
import type { SiteId } from "../types";

const VIEWS: { id: Exclude<View, null>; label: string }[] = [
  { id: "physics", label: "Physics" }, { id: "compare", label: "Compare" }, { id: "tree", label: "Why?" },
  { id: "money", label: "Money" }, { id: "guarantees", label: "Guarantees" }, { id: "impact", label: "Impact" },
  { id: "framework", label: "Sites" }, { id: "report", label: "Report" },
];
const SPEEDS = [1, 10, 30, 100];

function Clock() {
  const f = useStore((s) => s.frame);
  if (!f) return <div className="num text-ink-3 text-[11px] leading-tight">paused<br />press ▶ or Stress-test</div>;
  const d = new Date(f.time);
  const date = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  const time = d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  return (
    <div className="leading-tight">
      <div className="num text-[15px] font-semibold">{time} <span className="text-ink-3 text-[11px] font-normal hidden xl:inline">{date}</span></div>
      <div className="num text-[10.5px] text-ink-3">
        hour {f.hour_index + 1}/{f.hours_total} · {f.weather.t_out_c.toFixed(1)}°C outside
      </div>
    </div>
  );
}

export function ViewTabs() {
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  return (
    <div className="glass absolute right-3 top-[76px] h-[36px] w-[max(var(--rw),520px)] z-30 flex items-center justify-between px-1 overflow-x-auto" style={{ borderRadius: 10 }}>
      {VIEWS.map((v) => (
        <button key={v.id} onClick={() => setView(view === v.id ? null : v.id)}
                className={`px-1.5 py-1 rounded-md text-[11px] whitespace-nowrap ${view === v.id ? "bg-white/12 text-ink" : "text-ink-3 hover:text-ink-2"}`}>{v.label}</button>
      ))}
    </div>
  );
}

export function TopBar() {
  const s = useStore();
  const switchSite = (site: SiteId) => { if (site !== s.site) void s.setSite(site); };
  return (
    <div className="glass absolute top-3 left-3 right-3 h-[60px] flex items-center gap-3 px-3 z-30 whitespace-nowrap">
      <div className="flex items-center gap-2.5 pr-2">
        <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
          <defs><linearGradient id="fl" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stopColor="#ff3b3b" /><stop offset="0.6" stopColor="#f5a524" /><stop offset="1" stopColor="#22d3ee" /></linearGradient></defs>
          <path d="M16 3c3 5 9 9 9 16a9 9 0 0 1-18 0c0-4 2-7 5-9 0 3 1 6 3 6-1-5 0-9 1-13z" fill="url(#fl)" />
        </svg>
        <div className="leading-none">
          <div className="text-[17px] font-bold tracking-tight">Heat<span style={{ color: "#ff8a3d" }}>OS</span></div>
        </div>
      </div>

      <div className="flex rounded-lg p-0.5 bg-white/5 border border-line">
        {(["chelsea", "lansing"] as SiteId[]).map((site) => (
          <button key={site} onClick={() => switchSite(site)}
                  className={`relative px-3 py-1.5 text-[12.5px] font-medium rounded-md transition-colors ${s.site === site ? "text-ink" : "text-ink-3 hover:text-ink-2"}`}>
            {s.site === site && <motion.span layoutId="site" className="absolute inset-0 rounded-md bg-white/10" />}
            <span className="relative">{site === "chelsea" ? "Chelsea" : "Lansing"}</span>
            {site === "chelsea" && <span className="relative ml-1.5 text-[9px] text-accent align-top">★</span>}
          </button>
        ))}
      </div>

      <Clock />

      <div className="flex items-center gap-1.5">
        <button onClick={() => s.setPaused(!s.paused)} aria-label={s.paused ? "Play" : "Pause"}
                className="w-8 h-8 rounded-lg bg-white/5 border border-line hover:bg-white/10 grid place-items-center text-[13px]">
          {s.paused ? "▶" : "❚❚"}
        </button>
        <div className="flex rounded-lg bg-white/5 border border-line overflow-hidden">
          {SPEEDS.map((x) => (
            <button key={x} onClick={() => s.setSpeed(x)}
                    className={`num px-2 py-1.5 text-[11px] ${s.speed === x ? "bg-white/12 text-ink" : "text-ink-3 hover:text-ink-2"}`}>{x}×</button>
          ))}
        </div>
      </div>

      <button onClick={() => s.setAutopilot(!s.autopilot)} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-white/5 border border-line hover:bg-white/10">
        <span className="label">Autopilot</span>
        <span className={`relative w-9 h-5 rounded-full transition-colors ${s.autopilot ? "bg-accent/80" : "bg-white/15"}`}>
          <motion.span className="absolute top-0.5 w-4 h-4 rounded-full bg-white" animate={{ left: s.autopilot ? 18 : 2 }} />
        </span>
        <span className="num text-[11px] text-ink-2 w-9">{s.autopilot ? "MPC" : "Rules"}</span>
      </button>

      <div className="flex items-center gap-1 ml-1">
        {STEPS.map((st, i) => {
          const active = s.step === st.id;
          const done = STEPS.findIndex((x) => x.id === s.step) > i;
          return (
            <button key={st.id} onClick={() => s.setStep(st.id)}
                    className={`flex items-center gap-1 px-1.5 py-1 rounded-md text-[11.5px] ${active ? "text-ink bg-white/10" : done ? "text-ink-2" : "text-ink-3 hover:text-ink-2"}`}>
              <span className={`num w-4 h-4 rounded-full grid place-items-center text-[9.5px] ${active ? "bg-accent text-void" : done ? "bg-white/25 text-void" : "border border-line"}`}>{i + 1}</span>
              <span className={active ? "" : "hidden 2xl:inline"}>{st.label}</span>
            </button>
          );
        })}
      </div>

      <div className="flex-1" />

      <button onClick={() => s.setCinematic(!s.cinematic)} className="px-2.5 py-1.5 rounded-lg text-[12px] border border-line bg-white/5 hover:bg-white/10">
        {s.cinematic ? "■ Stop" : "◉ Cinematic"}
      </button>
      <button onClick={() => s.setDemo({ active: !s.demo.active, stage: s.demo.active ? 0 : 1 })}
              className="px-2.5 py-1.5 rounded-lg text-[12px] font-semibold text-void" style={{ background: "#ff8a3d" }}>
        {s.demo.active ? "Exit demo" : "Demo"}
      </button>
      <span title={s.live ? "Connected to the HeatOS engine" : "Backend offline: playing recorded runs"}
            className={`num text-[10px] px-1.5 py-0.5 rounded ${s.live ? "text-good" : "text-warning"}`}
            style={{ border: "1px solid currentColor" }}>{s.live == null ? "…" : s.live ? "LIVE" : "REPLAY"}</span>
    </div>
  );
}
