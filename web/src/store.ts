import { create } from "zustand";
import type { Bundle, Frame, SimEvent } from "./frame";
import type { SiteId } from "./types";
import { backendUp, loadBundle, startLive, startReplay, type Session } from "./lib/data";

export type Step = "analyze" | "recommend" | "stress" | "deal";
export type View = "transport" | "physics" | "compare" | "team" | "money" | "guarantees" | "impact" | "framework" | "tree" | "report" | null;
export const STEPS: { id: Step; label: string }[] = [
  { id: "analyze", label: "Analyze" },
  { id: "recommend", label: "Recommend" },
  { id: "stress", label: "Stress-test" },
  { id: "deal", label: "Deal & Report" },
];

interface State {
  live: boolean | null;
  site: SiteId;
  bundles: Partial<Record<SiteId, Bundle>>;
  loading: boolean;
  error: string | null;
  step: Step;
  frames: Frame[];
  frame: Frame | null;
  events: SimEvent[];
  session: Session | null;
  speed: number;
  paused: boolean;
  autopilot: boolean;
  selected: string | null;
  hovered: string | null;
  view: View;
  cinematic: boolean;
  finished: boolean;
  toast: string | null;
  demo: { active: boolean; stage: number; hint: string | null };
  showTeam: boolean;
  showInfra: boolean;
  toggleLayer(k: "showTeam" | "showInfra"): void;

  init(): Promise<void>;
  setSite(site: SiteId): Promise<void>;
  setStep(step: Step): void;
  select(id: string | null): void;
  hover(id: string | null): void;
  setView(v: View): void;
  fireScenario(name: string): Promise<void>;
  setAutopilot(on: boolean): Promise<void>;
  setSpeed(x: number): Promise<void>;
  setPaused(p: boolean): Promise<void>;
  setCinematic(on: boolean): void;
  notify(msg: string): void;
  setDemo(d: Partial<State["demo"]>): void;
}

const MAX_FRAMES = 240;

function mergeEvents(old: SimEvent[], add: SimEvent[]): SimEvent[] {
  if (!add.length) return old;
  const seen = new Set(old.map((e) => `${e.hour_index}|${e.text}`));
  const merged = [...old, ...add.filter((e) => !seen.has(`${e.hour_index}|${e.text}`))];
  return merged.sort((a, b) => a.hour_index - b.hour_index).slice(-60);
}

export const useStore = create<State>((set, get) => ({
  live: null,
  site: "chelsea",
  bundles: {},
  loading: true,
  error: null,
  step: "analyze",
  frames: [],
  frame: null,
  events: [],
  session: null,
  speed: 10,
  paused: true,
  autopilot: false,
  selected: null,
  hovered: null,
  view: null,
  cinematic: false,
  finished: false,
  toast: null,
  demo: { active: false, stage: 0, hint: null },
  showTeam: false,
  showInfra: true,
  toggleLayer: (k) => set((s) => ({ [k]: !s[k] }) as Partial<State>),

  async init() {
    const live = await backendUp();
    set({ live });
    await get().setSite("chelsea");
  },

  async setSite(site) {
    const { session, live } = get();
    session?.close();
    set({ site, loading: true, error: null, frames: [], frame: null, events: [], session: null, selected: null,
          finished: false, paused: get().step === "analyze" || get().step === "recommend" });
    try {
      const bundle = get().bundles[site] ?? (await loadBundle(site, !!live));
      set((s) => ({ bundles: { ...s.bundles, [site]: bundle } }));
      const onFrame = (f: Frame) => set((s) => {
        const frames = [...s.frames, f].slice(-MAX_FRAMES);
        const events = mergeEvents(s.events, f.events);
        return { frame: f, frames, events };
      });
      const onEvents = (ev: SimEvent[]) => set((s) => ({ events: mergeEvents(s.events, ev) }));
      const opts = { speed: get().speed, paused: get().paused, autopilot: get().autopilot };
      const sess = live
        ? await startLive(site, opts, onFrame, onEvents, (h) => set({ frames: h, frame: h[h.length - 1] ?? null }))
        : await startReplay(site, opts, onFrame, onEvents, () => set({ finished: true }));
      set({ session: sess, loading: false });
    } catch (e) {
      set({ loading: false, error: String(e) });
    }
  },

  setStep(step) {
    set({ step });
    if (step === "stress" || step === "deal") void get().setPaused(false);
    if (step === "deal") set({ view: "money" });
  },
  select: (id) => set({ selected: id }),
  hover: (id) => set({ hovered: id }),
  setView: (view) => set({ view }),

  async fireScenario(name) {
    const { session, step } = get();
    if (!session) return;
    if (step !== "stress" && step !== "deal") get().setStep("stress");
    await session.scenario(name);
    const label = get().bundles[get().site]?.scenarios.find((s) => s.name === name)?.label ?? name;
    get().notify(`Stress test fired: ${label}`);
  },

  async setAutopilot(on) {
    set({ autopilot: on });
    await get().session?.autopilot(on);
    if (!get().live) get().notify("Replay mode: MPC vs rules comparison shown from the recorded runs");
  },
  async setSpeed(x) { set({ speed: x }); await get().session?.speed(x); },
  async setPaused(p) { set({ paused: p }); await get().session?.pause(p); },
  setCinematic: (on) => set({ cinematic: on }),
  notify(msg) {
    set({ toast: msg });
    setTimeout(() => { if (get().toast === msg) set({ toast: null }); }, 3500);
  },
  setDemo: (d) => set((s) => ({ demo: { ...s.demo, ...d } })),
}));

export const useBundle = () => useStore((s) => s.bundles[s.site]);

if (import.meta.env.DEV) (window as unknown as { heatos: typeof useStore }).heatos = useStore;
