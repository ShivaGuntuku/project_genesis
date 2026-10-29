import Phaser from "phaser";
import { portrait } from "./art";
import type { AgentDetail, ServerMessage, Snapshot, WhyResult } from "./protocol";
import { WorldScene } from "./scene";
import * as ui from "./ui";
import "./style.css";

let socket: WebSocket | null = null;
let scene: WorldScene | null = null;
let game: Phaser.Game | null = null;
let snapshot: Snapshot | null = null;
let paused = false;
let speed = 1;
let selected: number | null = null;
let panelTimer: number | undefined;
const names = new Map<number, string>();

function send(cmd: object) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(cmd));
}

async function getJSON<T>(url: string): Promise<T | null> {
  const r = await fetch(url);
  return r.ok ? ((await r.json()) as T) : null;
}

// ------------------------------------------------------------------ inspect + why

async function refreshPanel() {
  if (selected === null || !scene) return;
  const d = await getJSON<AgentDetail>(`/api/agent/${selected}`);
  if (!d || selected !== d.id) return;
  ui.showPanel(d, (c) => portrait(scene!, scene!.agentTextureKey(d.id), c));
}

function select(id: number | null) {
  selected = id;
  window.clearInterval(panelTimer);
  if (id === null) {
    ui.hidePanel();
    return;
  }
  void refreshPanel();
  panelTimer = window.setInterval(refreshPanel, 1500);
}

async function why(id: number) {
  const w = await getJSON<WhyResult>(`/api/why/${id}`);
  if (w) ui.showWhy(w, (x, y) => scene?.panTo(x, y));
  else ui.toast("That event isn't saved yet; try again in a moment.");
}

// ------------------------------------------------------------------ game

function startGame(snap: Snapshot) {
  snapshot = snap;
  snap.agents.forEach((a) => names.set(a.id, a.name));
  ui.setNames([...names.values()]);
  ui.setClock(snap.world.name, snap.frame.clock, snap.frame.alive);
  ui.addEvents(snap.events);
  paused = snap.paused;
  speed = snap.speed;
  ui.setStatus(paused, speed);

  if (game) {
    game.destroy(true);
    scene = null;
  }
  scene = new WorldScene({
    onSelect: (id) => select(id),
    onReady: () => scene?.setSpeed(speed),
  });
  game = new Phaser.Game({
    type: Phaser.AUTO,
    parent: "game",
    pixelArt: true,
    roundPixels: false,
    backgroundColor: "#0b0f14",
    scale: { mode: Phaser.Scale.RESIZE, width: "100%", height: "100%" },
    scene: [],
  });
  game.scene.add("world", scene, true, { snapshot: snap });
}

function onMessage(msg: ServerMessage) {
  switch (msg.type) {
    case "snapshot":
      startGame(msg);
      break;
    case "tick":
      if (!snapshot) return;
      ui.setClock(snapshot.world.name, msg.clock, msg.alive);
      if (msg.newAgents?.length) {
        msg.newAgents.forEach((a) => names.set(a.id, a.name));
        ui.setNames([...names.values()]);
      }
      ui.addEvents(msg.events);
      if (scene?.ready) {
        try {
          scene.applyFrame(msg);
        } catch (err) {
          console.error("Drawing failed for tick", msg.tick, err); // keep the feed alive
        }
      }
      break;
    case "status":
      paused = msg.paused;
      speed = msg.speed;
      scene?.setSpeed(speed);
      ui.setStatus(paused, speed);
      if (msg.message) ui.toast(msg.message);
      break;
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws`);
  socket.onmessage = (e) => onMessage(JSON.parse(e.data) as ServerMessage);
  socket.onclose = () => {
    ui.toast("Disconnected from the world. Reconnecting…");
    setTimeout(connect, 2000);
  };
}

// ------------------------------------------------------------------ wiring

ui.setupTopBar({
  onPlayPause: () => send({ cmd: paused ? "play" : "pause" }),
  onSpeed: (s) => send({ cmd: "speed", value: s }),
  onLinks: (show) => scene && (scene.showLinks = show),
  onFind: (name) => {
    const entry = [...names.entries()].find(([, n]) => n.toLowerCase() === name.toLowerCase());
    if (!entry) return ui.toast(`Nobody called "${name}"`);
    scene?.follow(entry[0]);
    select(entry[0]);
  },
});
ui.setupFeed((x, y) => scene?.panTo(x, y), (id) => void why(id));
ui.setupPanel(() => select(null), (id) => void why(id), (id) => {
  scene?.follow(id);
  select(id);
});

connect();
