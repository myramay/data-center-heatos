import { Component, useEffect, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useStore } from "./store";
import { Scene } from "./scene/Scene";
import { TopBar, ViewTabs } from "./ui/TopBar";
import { BuildingList, RightPanel } from "./ui/Sides";
import { Dock } from "./ui/Dock";
import { Views } from "./ui/Views";
import { DemoController } from "./ui/Demo";
import { Locator } from "./ui/Locator";

/** Keeps the control room usable if WebGL is unavailable (old GPU, sandboxed browser). */
class SceneBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) {
      return (
        <div className="absolute inset-0 grid place-items-center" style={{ background: "radial-gradient(ellipse at 50% 40%, #101a33, #05070d 70%)" }}>
          <div className="text-center text-ink-3 text-[12px] mt-[-120px]">3D view unavailable in this browser (WebGL could not start).<br />All panels and data still work.</div>
        </div>
      );
    }
    return this.props.children;
  }
}

function Legend() {
  const step = useStore((s) => s.step);
  const items = step === "analyze"
    ? [["#3b2a8f", "low heat use"], ["#22d3ee", ""], ["#f5a524", ""], ["#ff3b3b", "high heat use"]]
    : [["#ff7a1a", "on network heat"], ["#f7b500", "drawing on storage"], ["#e5262b", "on backup boiler"], ["#7f97b8", "not connected"], ["#f2f4f7", "other buildings"]];
  return (
    <div className="absolute left-[calc(var(--lw)+24px)] bottom-[232px] z-10 flex items-center gap-3 px-3 py-1.5 glass" style={{ borderRadius: 10 }}>
      {items.map(([c, l], i) => (
        <span key={i} className="flex items-center gap-1.5 text-[10.5px] text-ink-2">
          <span className="w-2.5 h-2.5 rounded-sm" style={{ background: c, border: "1px solid rgb(255 255 255 / 0.25)" }} />{l}
        </span>
      ))}
    </div>
  );
}

export default function App() {
  const init = useStore((s) => s.init);
  const loading = useStore((s) => s.loading);
  const error = useStore((s) => s.error);
  const toast = useStore((s) => s.toast);
  const cinematic = useStore((s) => s.cinematic);
  useEffect(() => { void init(); }, [init]);

  return (
    <div className="relative w-full h-full">
      <div className="absolute inset-0" style={{ background: "radial-gradient(ellipse at 50% 32%, #f7f6f2 0%, #e6e7ea 52%, #c9ccd2 100%)" }}>
        <SceneBoundary><Scene /></SceneBoundary>
      </div>
      <AnimatePresence>
        {!cinematic && (
          <motion.div key="ui" className="absolute inset-0 pointer-events-none [&>*]:pointer-events-auto" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <TopBar />
            <ViewTabs />
            <BuildingList />
            <RightPanel />
            <Dock />
            <Views />
            <Legend />
            <Locator />
          </motion.div>
        )}
      </AnimatePresence>
      <div className="absolute right-3 bottom-[222px] z-10 text-[9.5px] text-ink-3/90 px-1.5 py-0.5 rounded" style={{ background: "rgb(255 255 255 / 0.55)", color: "#4b5563" }}>
        Geography: US Census Bureau · Buildings © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors
      </div>
      <DemoController />
      <AnimatePresence>
        {toast && (
          <motion.div key={toast} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                      className="absolute top-[84px] left-1/2 -translate-x-1/2 z-50 glass px-4 py-2 text-[12.5px]">{toast}</motion.div>
        )}
      </AnimatePresence>
      {(loading || error) && (
        <div className="absolute inset-0 z-50 grid place-items-center bg-void/80">
          <div className="text-center">
            <div className="text-[20px] font-bold">Heat<span className="text-accent">OS</span></div>
            <div className="text-[12px] text-ink-3 mt-2">{error ?? "Loading site model…"}</div>
          </div>
        </div>
      )}
    </div>
  );
}
