import { createContext, useContext, useEffect, useRef, useState } from "react";

// ------------------------------------------------------------------ types

export interface EventItem {
  id: number; tick: number; clock: string; type: string; act: string | null; text: string;
  actors: number[]; targets: number[]; lie: boolean; thought: string | null; hasCause: boolean;
  god?: boolean; // M9: the creator's hand
}

export interface Overview {
  world: string; seed: number; tick: number; clock: string; day: number; savedAt: string;
  running: boolean; llmEnabled: boolean;
  people: { alive: number; ever: number; born: number; generations: number; deaths: Record<string, number> };
  messages: { total: number; voiced: number; lies: number; lastDay: number };
  llm: { total: number; outcomes: Record<string, number>; avg_ms: number | null; recentMs: number | null };
  dbBytes: number; feed: EventItem[];
  conflict: { fights: number; killed: number; groups: number; groupsEver: number }; population: { day: number; alive: number }[];
}

export interface Effect { who: string; about: string; dimension: string; delta: number }
export interface Message {
  id: number; from: number; to: number; fromName: string; toName: string; act: string;
  answer: string | null; text: string; line: string | null; thought: string | null;
  source: string; truthful: boolean; replyTo: number | null; effects: Effect[];
}
export interface Thread {
  key: string; tick: number; clock: string; a: { id: number; name: string };
  b: { id: number; name: string }; messages: Message[]; voiced: number;
}
export interface Threads { total: number; page: number; pageSize: number; threads: Thread[] }

export interface DayLanguage {
  day: number; messages: number; conversations: number; turnsPerConversation: number;
  voicedShare: number; wordsPerLine: number; distinctWords: number; newWords: number;
  vocabulary: number; lieRate: number; pairs: number; speakers: number;
  acts: Record<string, number>; answers: Record<string, number>;
}
export interface WordUse { word: string; day: number; speaker: number; eventId: number; line: string; count?: number }
export interface Language {
  series: DayLanguage[];
  words: {
    totalWords: number; vocabulary: number; top: { word: string; count: number }[];
    rising: { word: string; count: number; ratio: number }[]; newest: WordUse[];
    valley: WordUse[]; dictionary: boolean;
  };
  names: Record<string, string>;
}

export interface AgentRow {
  id: number; name: string; alive: boolean; stage: string; generation: number; ageDays: number;
  parents: string[]; partner: string | null; children: number; causeOfDeath: string | null;
  died: string | null; messages: number; voiced: number; friends: number; rivals: number;
  beliefs: number; story: string;
}

export interface Relation {
  id: number; name: string; alive: boolean; label: string; theirLabel: string | null;
  theirAffinity: number | null; affinity: number; trust: number; respect: number; fear: number;
  gratitude: number; grievance: number; familiarity: number; family: string | null;
}
export interface AgentDetail {
  id: number; name: string; alive: boolean; stage: string; generation: number; ageDays: number;
  born: string | null; died: string | null; causeOfDeath: string | null; position: [number, number];
  action: string | null; needs: Record<string, number>; carrying: Record<string, number>;
  talent: string | null; work: Record<string, number>; owes: { to: number; name: string; good: string }[];
  goal: { need: string; text: string; since: string; ownWords: boolean } | null;
  goals: { clock: string; type: string; text: string; eventId: number }[];
  story: string; stories: { clock: string; story: string | null; beliefs: string[]; thought: string | null; eventId: number }[];
  partner: number | null; pregnant: boolean;
  family: { id: number; name: string; relation: string; alive: boolean }[];
  genes: Record<string, number>; parentGenes: { name: string; genes: Record<string, number> }[];
  skills: Record<string, number>; relationships: Relation[];
  said: { messages: number; voiced: number; lies: number };
}

export interface LlmCall {
  id: number; clock: string; agent: string | null; other: string | null; kind: string;
  model: string; outcome: string; error: string | null; ms: number | null; choice: string | null;
  say: string | null; thought: string | null; eventId?: number | null;
}

// ------------------------------------------------------------------ fetching

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json() as Promise<T>;
}

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "" || v === false) continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Live tick from the server; pages refetch when it changes (if live is on). */
export const LiveContext = createContext<{ tick: number; live: boolean }>({ tick: 0, live: true });

export function useLiveTick(): { tick: number; clock: string; connected: boolean } {
  const [state, setState] = useState({ tick: 0, clock: "", connected: false });
  useEffect(() => {
    const source = new EventSource("/api/live");
    source.onmessage = (e) => {
      const d = JSON.parse(e.data);
      setState({ tick: d.tick, clock: d.clock, connected: true });
    };
    source.onerror = () => setState((s) => ({ ...s, connected: false }));
    return () => source.close();
  }, []);
  return state;
}

/** Fetch `path`; keep showing the previous data while reloading (no flash). */
export function useApi<T>(path: string | null, follow = true) {
  const { tick, live } = useContext(LiveContext);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const liveTick = follow && live ? tick : 0;
  const latest = useRef(0);
  useEffect(() => {
    if (!path) return;
    const id = ++latest.current;
    setLoading(true);
    get<T>(path)
      .then((d) => { if (id === latest.current) { setData(d); setError(null); } })
      .catch((e) => { if (id === latest.current) setError(String(e)); })
      .finally(() => { if (id === latest.current) setLoading(false); });
  }, [path, liveTick]);
  return { data, error, loading };
}

// ------------------------------------------------------------------ routing (hash)

export function useRoute(): { path: string[]; params: URLSearchParams } {
  const read = () => {
    const raw = window.location.hash.replace(/^#\/?/, "");
    const [p, q] = raw.split("?");
    return { path: p ? p.split("/") : [], params: new URLSearchParams(q || "") };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function go(hash: string) {
  window.location.hash = hash;
}

export function setParams(base: string, params: Record<string, string | number | boolean | null | undefined>) {
  go(base + query(params));
}
