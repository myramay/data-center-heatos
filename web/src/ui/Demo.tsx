import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useBundle, useStore } from "../store";

const STAGES = [
  "",
  "Analyze: every candidate building, coloured by how much heat it uses",
  "Recommend: pipes reach the buildings worth connecting. Here's why one public-housing tower made the cut",
  "Stress-test: press Polar vortex (key 3)",
  "Recovered. Now switch to Lansing (key 4)",
  "Lansing: press Bitcoin price crash (key 5)",
  "Generate the report (key 6)",
];

/** Scripted pitch sequence. Keys 1-6 jump to each step; Q-T fire stress tests; C = cinematic. */
export function DemoController() {
  const demo = useStore((s) => s.demo);
  const s = useStore.getState;
  const bundle = useBundle();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const go = async (stage: number) => {
    const st = s();
    st.setDemo({ active: true, stage });
    if (timer.current) clearTimeout(timer.current);
    if (stage === 1) {
      if (st.site !== "chelsea") await st.setSite("chelsea");
      st.setView(null); st.select(null); st.setStep("analyze");
      timer.current = setTimeout(() => go(2), 5000);
    } else if (stage === 2) {
      st.setStep("recommend");
      const b = s().bundles.chelsea;
      const pick = b?.plan.items.find((i) => i.connect && b.buildings.find((x) => x.id === i.building_id)?.use_type === "public_housing");
      timer.current = setTimeout(() => { if (pick) s().select(pick.building_id); }, 2600);
      timer.current = setTimeout(() => { s().select(pick?.building_id ?? null); go(3); }, 9000);
    } else if (stage === 3) {
      st.select(null); st.setStep("stress"); void st.setSpeed(10);
    } else if (stage === 4) {
      st.setView(null);
    } else if (stage === 5) {
      if (st.site !== "lansing") await st.setSite("lansing");
      st.setStep("stress");
    } else if (stage === 6) {
      st.setStep("deal"); st.setView("report");
    }
  };

  // after a stress test fires in stage 3/5, advance once the system has had time to react/recover
  const frame = useStore((x) => x.frame);
  const fired = useRef<number | null>(null);
  useEffect(() => {
    if (!demo.active || !frame) return;
    const active = frame.active_scenarios;
    if (demo.stage === 3 && active.includes("polar_vortex") && fired.current == null) fired.current = frame.hour_index;
    if (demo.stage === 3 && fired.current != null && (frame.hour_index - fired.current > 80 || (!active.includes("polar_vortex") && frame.hour_index - fired.current > 10))) {
      fired.current = null; void go(4);
    }
    if (demo.stage === 5 && active.includes("bitcoin_price_crash") && fired.current == null) fired.current = frame.hour_index;
    if (demo.stage === 5 && fired.current != null && frame.hour_index - fired.current > 30) { fired.current = null; void go(6); }
  }, [frame, demo]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      const st = s();
      if (e.key >= "1" && e.key <= "6") {
        const n = Number(e.key);
        if (n === 3 && st.demo.stage === 3) { void st.fireScenario("polar_vortex"); return; }
        if (n === 5 && st.demo.stage === 5) { void st.fireScenario("bitcoin_price_crash"); return; }
        void go(n);
      }
      const idx = ["q", "w", "e", "r", "t"].indexOf(e.key.toLowerCase());
      const scen = st.bundles[st.site]?.scenarios[idx];
      if (idx >= 0 && scen) void st.fireScenario(scen.name);
      if (e.key.toLowerCase() === "c") st.setCinematic(!st.cinematic);
      if (e.key === " ") { e.preventDefault(); void st.setPaused(!st.paused); }
      if (e.key === "Escape") { st.select(null); st.setView(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (demo.active && demo.stage === 1) void go(1); }, [demo.active]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  void bundle;

  return (
    <AnimatePresence>
      {demo.active && demo.stage > 0 && (
        <motion.div key={demo.stage} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
                    className="absolute left-[calc((100vw-var(--sw))/2)] -translate-x-1/2 bottom-16 z-50 glass px-5 py-3 flex items-center gap-4"
                    style={{ borderColor: "#ff8a3d55" }}>
          <span className="num text-[11px] text-accent">STEP {demo.stage}/6</span>
          <span className="text-[13.5px]">{STAGES[demo.stage]}</span>
          <div className="flex gap-1">
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <button key={n} onClick={() => go(n)} className={`num w-5 h-5 rounded text-[10px] ${n === demo.stage ? "bg-accent text-void" : "bg-white/10 text-ink-3"}`}>{n}</button>
            ))}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
