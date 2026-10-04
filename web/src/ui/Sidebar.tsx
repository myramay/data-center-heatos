import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { useBundle, useStore, type View } from "../store";
import { BareContext } from "./bits";
import { BuildingDetail, BuildingList, ConfidencePanel } from "./Sides";
import { EventTicker, FlowChart, StressToggles } from "./Dock";

type Tab = "live" | "buildings" | "analysis" | "log";
const TABS: { id: Tab; label: string }[] = [
  { id: "live", label: "Live" }, { id: "buildings", label: "Buildings" }, { id: "analysis", label: "Analysis" }, { id: "log", label: "Log" },
];

const KEY_VIEWS: { id: Exclude<View, null>; label: string; hint: string }[] = [
  { id: "transport", label: "How should the heat travel?", hint: "Every transport method, designed and stress-tested" },
  { id: "compare", label: "Best deal for everyone?", hint: "Costs, incentives and carbon vs the alternatives" },
  { id: "tree", label: "Why these buildings?", hint: "The decision tree behind the plan" },
  { id: "physics", label: "Network physics", hint: "Temperatures, flows and the energy balance, live" },
];
const MORE_VIEWS: { id: Exclude<View, null>; label: string }[] = [
  { id: "money", label: "Who pays whom" }, { id: "guarantees", label: "Heat guarantees" }, { id: "impact", label: "Sustainability impact" },
  { id: "team", label: "Team model (merged)" }, { id: "framework", label: "Chelsea vs Lansing" }, { id: "report", label: "Report: five deliverables" },
];

function Toggle({ label, on, value, onClick }: { label: string; on: boolean; value?: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-white/5 border border-line hover:bg-white/10 flex-1 min-w-0">
      <span className="label">{label}</span>
      <span className={`relative w-9 h-5 rounded-full transition-colors ml-auto ${on ? "bg-accent/80" : "bg-white/15"}`}>
        <motion.span className="absolute top-0.5 w-4 h-4 rounded-full bg-white" animate={{ left: on ? 18 : 2 }} />
      </span>
      {value && <span className="num text-[11px] text-ink-2 w-9 text-left">{value}</span>}
    </button>
  );
}

function LiveTab() {
  const s = useStore();
  return (
    <div className="flex flex-col gap-1 pb-3">
      <StressToggles />
      <div className="flex gap-2 px-3 pb-1">
        <Toggle label="Autopilot" on={s.autopilot} value={s.autopilot ? "MPC" : "Rules"} onClick={() => void s.setAutopilot(!s.autopilot)} />
        <Toggle label="Cinematic" on={s.cinematic} onClick={() => s.setCinematic(!s.cinematic)} />
      </div>
      <div className="h-px bg-line mx-4 my-1" />
      <ConfidencePanel />
      <div className="h-px bg-line mx-4 my-1" />
      <FlowChart />
    </div>
  );
}

const TEAM_PAGES = [
  { href: "/team/offer.html", label: "Heat offer", hint: "price per unit of heat vs today" },
  { href: "/team/index.html", label: "Build plan", hint: "the team's map; click a building for its offer" },
  { href: "/team/proposal.html", label: "System proposal", hint: "the team's written proposal" },
];

function AnalysisTab() {
  const site = useStore((s) => s.site);
  const firstOffer = useBundle()?.team?.chosen?.[0]?.id?.split("-")[1];       // team plan's first customer (BBL)
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const open = (id: Exclude<View, null>) => setView(view === id ? null : id);
  return (
    <div className="px-3 pb-3">
      <div className="label px-1 pt-3 pb-2">Key questions</div>
      <div className="flex flex-col gap-2">
        {KEY_VIEWS.map((v) => (
          <button key={v.id} onClick={() => open(v.id)}
                  className={`text-left px-3 py-2.5 rounded-lg border transition-colors ${view === v.id ? "border-accent bg-accent/15" : "border-line bg-white/5 hover:bg-white/10"}`}>
            <div className="text-[13px] font-semibold">{v.label}</div>
            <div className="text-[11px] text-ink-3 mt-0.5">{v.hint}</div>
          </button>
        ))}
      </div>
      <div className="label px-1 pt-4 pb-2">More detail</div>
      <div className="grid grid-cols-2 gap-1.5">
        {MORE_VIEWS.map((v) => (
          <button key={v.id} onClick={() => open(v.id)}
                  className={`text-left px-2.5 py-2 rounded-lg text-[12px] border transition-colors ${view === v.id ? "border-accent bg-accent/15 text-ink" : "border-line text-ink-2 hover:bg-white/5"}`}>{v.label}</button>
        ))}
      </div>
      <div className="label px-1 pt-4 pb-2">Team pages</div>
      <div className="flex flex-col gap-1.5">
        {TEAM_PAGES.map((t) => (
          <a key={t.href} href={t.href + `?site=${site}` + (t.href.endsWith("offer.html") && firstOffer ? `&bbl=${firstOffer}` : "")}
             target="_blank" rel="noreferrer"
             className="flex items-center justify-between px-2.5 py-2 rounded-lg text-[12px] border border-line text-ink-2 hover:bg-white/5">
            <span><span className="text-ink">{t.label}</span> <span className="text-ink-3">· {t.hint}</span></span><span aria-hidden>↗</span>
          </a>
        ))}
      </div>
    </div>
  );
}

/** Everything that isn't the map lives here, on the right, one tab at a time. */
export function Sidebar() {
  const bundle = useBundle();
  const selected = useStore((s) => s.selected);
  const view = useStore((s) => s.view);
  const step = useStore((s) => s.step);
  const events = useStore((s) => s.events.length);
  const [tab, setTab] = useState<Tab>("live");
  // follow what the user (or the demo) just did elsewhere
  useEffect(() => { if (selected) setTab("buildings"); }, [selected]);
  useEffect(() => { if (view) setTab("analysis"); }, [view]);
  useEffect(() => { if (step === "stress") setTab("live"); }, [step]);

  return (
    <div className="glass absolute right-3 top-[76px] bottom-3 w-[var(--sw)] z-30 flex flex-col overflow-hidden">
      <div className="flex gap-1 p-1.5 border-b border-line shrink-0">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
                  className={`relative flex-1 py-1.5 rounded-md text-[12px] font-medium transition-colors ${tab === t.id ? "text-ink" : "text-ink-3 hover:text-ink-2"}`}>
            {tab === t.id && <motion.span layoutId="sidetab" className="absolute inset-0 rounded-md bg-white/10" />}
            <span className="relative">
              {t.label}
              {t.id === "buildings" && <span className="num ml-1 text-[10px] text-ink-3">{bundle?.buildings.length ?? ""}</span>}
              {t.id === "log" && events > 0 && <span className="num ml-1 text-[10px] text-accent">{events}</span>}
            </span>
          </button>
        ))}
      </div>
      <BareContext.Provider value={true}>
        <div className="flex-1 min-h-0 overflow-y-auto scroll-thin">
          {tab === "live" && <LiveTab />}
          {tab === "buildings" && (selected ? <BuildingDetail id={selected} /> : <BuildingList />)}
          {tab === "analysis" && <AnalysisTab />}
          {tab === "log" && <EventTicker />}
        </div>
      </BareContext.Provider>
    </div>
  );
}
