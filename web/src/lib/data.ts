// Data layer: live backend when reachable, otherwise Replay mode from recordings/.
import type { Bundle, Frame } from "../frame";
import type { SiteId } from "../types";

export const API = (import.meta.env.VITE_API as string | undefined) ?? "http://localhost:8000";
const WS = API.replace(/^http/, "ws");

export async function backendUp(): Promise<boolean> {
  if (API === "none") return false;            // static hosting (Vercel): replay the recorded runs only
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 1500);
    const r = await fetch(`${API}/health`, { signal: ctl.signal });
    clearTimeout(t);
    return r.ok;
  } catch {
    return false;
  }
}

export async function loadBundle(site: SiteId, live: boolean): Promise<Bundle> {
  // Recorded bundles carry the report and the autopilot comparison; prefer them when present.
  const rec = await fetch(`/recordings/${site}_bundle.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!live) {
    if (!rec) throw new Error("No recordings found. Run `make record`.");
    return rec as Bundle;
  }
  const b = (await fetch(`${API}/bundle?site=${site}`).then((r) => r.json())) as Bundle;
  if (rec) {
    b.report_md = rec.report_md;
    b.autopilot_compare = rec.autopilot_compare;
    b.alternatives = b.alternatives ?? rec.alternatives;
  }
  return b;
}

// ------------------------------------------------------------------ live runs

export interface Session {
  scenario(name: string): Promise<void>;
  autopilot(on: boolean): Promise<void>;
  speed(x: number): Promise<void>;
  pause(p: boolean): Promise<void>;
  close(): void;
  runId: string | null;
  live: boolean;
}

type FrameCb = (f: Frame) => void;
type EventsCb = (e: Frame["events"]) => void;

export async function startLive(site: SiteId, opts: { speed: number; autopilot: boolean; paused: boolean },
                                onFrame: FrameCb, onEvents: EventsCb, onHistory: (h: Frame[]) => void): Promise<Session> {
  const res = await fetch(`${API}/run`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site, speed: opts.speed, autopilot: opts.autopilot, autostart: !opts.paused, hours: 336 }),
  }).then((r) => r.json());
  const runId: string = res.run_id;
  const ws = new WebSocket(`${WS}/stream/${runId}`);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "frame") onFrame(m as Frame);
    else if (m.type === "hello") onHistory(m.history as Frame[]);
    else if (m.type === "events") onEvents(m.events);
  };
  const post = (path: string, body: object) =>
    fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" },
                             body: JSON.stringify({ run_id: runId, ...body }) }).then(() => undefined);
  return {
    runId, live: true,
    scenario: (name) => post("/scenario", { name }),
    autopilot: (on) => post("/autopilot", { on }),
    speed: (x) => post("/speed", { multiplier: x }),
    pause: (p) => post("/pause", { paused: p }),
    close: () => { ws.close(); fetch(`${API}/run/${runId}`, { method: "DELETE" }).catch(() => {}); },
  };
}

// ------------------------------------------------------------------ replay

interface Recording { site: SiteId; scenario: string | null; fire_at: number | null; hours: number; frames: Frame[] }

const cache = new Map<string, Recording>();
async function recording(site: SiteId, name: string): Promise<Recording> {
  const key = `${site}_${name}`;
  if (!cache.has(key)) cache.set(key, await fetch(`/recordings/${key}.json`).then((r) => r.json()));
  return cache.get(key)!;
}

export async function startReplay(site: SiteId, opts: { speed: number; paused: boolean },
                                  onFrame: FrameCb, onEvents: EventsCb, onFinish: () => void): Promise<Session> {
  let rec = await recording(site, "baseline");
  let i = 0;
  let speed = opts.speed;
  let paused = opts.paused;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const tick = () => {
    if (!paused && i < rec.frames.length) {
      const f = { ...rec.frames[i], speed, paused, run_id: "replay" };
      onFrame(f);
      i += 1;
      if (i >= rec.frames.length) onFinish();
    }
    timer = setTimeout(tick, 1000 / speed);
  };
  timer = setTimeout(tick, 50);
  return {
    runId: null, live: false,
    async scenario(name) {
      const next = await recording(site, name);
      const at = next.fire_at ?? 0;
      rec = next;
      i = at;                       // recordings fire the stress test at hour `fire_at`
      onEvents(next.frames[at]?.events ?? []);
    },
    async autopilot() { /* MPC comparison comes from the recorded bundle in Replay mode */ },
    async speed(x) { speed = x; },
    async pause(p) { paused = p; },
    close() { if (timer) clearTimeout(timer); },
  };
}
