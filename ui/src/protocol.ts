// Messages from the Genesis server (see src/genesis/api/views.py).

export type Facing = "down" | "left" | "right" | "up";
export type Icon = "sleep" | "talk" | "thirsty" | "hungry" | "search" | "angry" | null;

export interface Looks {
  skin: number;
  hair: number;
  eyes: number;
  strength: number;
}

export interface AgentInfo {
  id: number;
  name: string;
  generation: number;
  lineage: number;
  looks: Looks;
}

export interface AgentState {
  id: number;
  x: number;
  y: number;
  facing: Facing;
  action: string | null;
  stage: "child" | "adult" | "elder";
  alive: boolean;
  health: number;
  hunger: number;
  thirst: number;
  energy: number;
  icon: Icon;
  group: number | null; // M7: the group they belong to (colours the name tag)
}

export interface EventItem {
  id: number;
  type: string;
  clock: string;
  text: string;
  actors: number[];
  targets: number[];
  x: number | null;
  y: number | null;
  act: string | null;
  lie: boolean;
  bubble: string | null;
  thought: string | null; // the speaker's private reason (LLM mind, from M4)
  voiced: boolean; // said in the agent's own words (LLM)
  float: string | null;
  routine: boolean;
  hasCause: boolean;
}

export type Link = [
  number,
  number,
  "friend" | "close friend" | "rival" | "enemy" | "partner" | "family",
];

export interface Frame {
  type: "tick";
  tick: number;
  clock: string;
  hour: number;
  alive: number;
  agents: AgentState[];
  food: number[];
  links: Link[];
  events: EventItem[];
  newAgents?: AgentInfo[]; // born since the last frame (M6)
  dryLakes?: number[]; // lakes in drought (M7), numbered as the server numbers them
}

export interface WorldInfo {
  name: string;
  width: number;
  height: number;
  tiles: string[]; // one string per row; p=plains f=forest m=mountain w=water k=peak
  food: { id: number; x: number; y: number; max: number }[];
  start_hour: number;
}

export interface Snapshot {
  type: "snapshot";
  world: WorldInfo;
  agents: AgentInfo[];
  frame: Frame;
  events: EventItem[];
  paused: boolean;
  speed: number;
}

export interface Status {
  type: "status";
  paused: boolean;
  speed: number;
  message?: string;
}

export type ServerMessage = Snapshot | Frame | Status;

export interface Relation {
  id: number;
  name: string;
  alive: boolean;
  label: string;
  theirLabel: string | null;
  affinity: number;
  trust: number;
  gratitude: number;
  grievance: number;
  fear: number;
  met: boolean;
}

export interface AgentDetail extends AgentInfo {
  alive: boolean;
  stage: string;
  causeOfDeath: string | null;
  died: string | null;
  ageDays: number;
  lifespanDays: number;
  position: [number, number];
  action: string | null;
  needs: { health: number; hunger: number; thirst: number; energy: number };
  carrying: { food: number; water: number };
  character: string[];
  strengths: string[];
  weaknesses: string[];
  skills: string[];
  genes: Record<string, number>;
  vision: number;
  knowsFood: number;
  knowsWater: boolean;
  relationships: Relation[];
  events: EventItem[];
  family: { id: number; name: string; relation: string; alive: boolean }[]; // M6
  expecting: boolean;
  group: { name: string; leader: string | null; leaderId: number; members: number; order: string | null } | null;
  story: string; // self-story in their own words (nightly reflection, M5)
  goal: { need: string; text: string; since: string; ownWords: boolean } | null; // M8c
  drives: Record<string, number> | null;
  talent: string | null; // M8d
  profession: string | null;
  beliefs: { text: string; clock: string }[];
  memories: { text: string; clock: string; importance: number }[]; // what weighs most
}

export interface WhyResult {
  event: number;
  chain: EventItem[];
  consequences: EventItem[];
}
