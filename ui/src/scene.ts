import Phaser from "phaser";
import {
  T, buildTerrain, buildThings, characterTexture, shoreTexture,
} from "./art";
import type {
  AgentInfo, AgentState, EventItem, Frame, Link, Snapshot,
} from "./protocol";

const DEPTH = { ground: 0, things: 5, links: 8, agents: 10, night: 900, names: 1000, bubbles: 1100 };
const LINK_COLOURS: Record<string, number> = {
  friend: 0x55efc4, "close friend": 0x00b894, rival: 0xfdcb6e, enemy: 0xe17055,
  partner: 0xff7eb6, family: 0xf9ca24,
};
const FLOAT_COLOURS: Record<string, string> = {
  skill_improved: "#ffeaa7", discovered: "#81ecec", life_stage: "#dfe6e9",
  lie_discovered: "#ff7675", relation_changed: "#fab1a0", died: "#b2bec3",
  birth: "#ffeaa7", conceived: "#ff7eb6", grieved: "#b2bec3",
  fight: "#ff4757", founded_group: "#74b9ff", left_group: "#b2bec3", leader_challenged: "#ffeaa7",
  reflected: "#a29bfe", invented: "#ffd32a", built: "#e1b382", learned: "#7bed9f",
  goal_set: "#70a1ff", goal_reached: "#ffd32a", promise_broken: "#ff7675",
  fell_sick: "#badc58", beast_attack: "#ff4757", migrated: "#74b9ff",
};
const GRAVE_TICKS = 72; // graves fade after 3 days
// Group colours for name tags (M7), in a fixed order by group id.
const GROUP_COLOURS = ["#2a78d6", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#6250d6", "#e34948"];

/** Lakes numbered exactly as the server does: connected water, columns left to right. */
function numberLakes(tiles: string[], width: number, height: number): Map<string, number> {
  const ids = new Map<string, number>();
  let count = 0;
  for (let x = 0; x < width; x++)
    for (let y = 0; y < height; y++) {
      if (tiles[y]?.[x] !== "w" || ids.has(`${x},${y}`)) continue;
      count++;
      const stack: [number, number][] = [[x, y]];
      ids.set(`${x},${y}`, count);
      while (stack.length) {
        const [cx, cy] = stack.pop()!;
        for (const [nx, ny] of [[cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]]) {
          if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
          if (tiles[ny]?.[nx] === "w" && !ids.has(`${nx},${ny}`)) {
            ids.set(`${nx},${ny}`, count);
            stack.push([nx, ny]);
          }
        }
      }
    }
  return ids;
}

const sheetKey = (id: number, stage: string) => `agent-${id}-${stage === "elder" ? "old" : "young"}`;

export interface SceneHooks {
  onSelect(id: number | null): void;
  onReady(): void;
}

class AgentView {
  sprite: Phaser.GameObjects.Sprite;
  label: Phaser.GameObjects.Text;
  icon: Phaser.GameObjects.Image;
  bubble: Phaser.GameObjects.Container | null = null;
  bubbleUntil = 0;
  queue: { text: string; lie: boolean }[] = []; // lines waiting for the current bubble to end
  state: AgentState | null = null;
  stage = "adult";
  group: number | null = null;
  sheet = ""; // current sprite-sheet key (changes when the agent becomes an elder)
  moving: Phaser.Tweens.Tween | null = null;

  constructor(public info: AgentInfo, scene: Phaser.Scene) {
    this.sheet = sheetKey(info.id, "adult");
    this.sprite = scene.add.sprite(0, 0, this.sheet, "down-0").setDepth(DEPTH.agents);
    this.sprite.setInteractive({ useHandCursor: true, pixelPerfect: false });
    this.label = scene.add
      .text(0, 0, info.name, {
        fontFamily: "monospace", fontSize: "5px", color: "#ffffff",
        stroke: "#000000", strokeThickness: 1.5, resolution: 8,
      })
      .setOrigin(0.5, 1)
      .setDepth(DEPTH.names);
    this.icon = scene.add.image(0, 0, "icon-talk").setDepth(DEPTH.names).setVisible(false);
  }

  get x() { return this.sprite.x; }
  get y() { return this.sprite.y; }

  place() {
    this.label.setPosition(this.sprite.x, this.sprite.y - 7 * this.sprite.scaleY);
    this.icon.setPosition(this.sprite.x, this.sprite.y - 7 * this.sprite.scaleY - 9);
    this.sprite.setDepth(DEPTH.agents + this.sprite.y / 1000);
    if (this.bubble) this.bubble.setPosition(this.sprite.x, this.sprite.y - 7 * this.sprite.scaleY - 6);
  }
}

export class WorldScene extends Phaser.Scene {
  private snap!: Snapshot;
  private agents = new Map<number, AgentView>();
  private bushes: Phaser.GameObjects.Image[] = [];
  private foodMax: number[] = [];
  private water: Phaser.GameObjects.Image[] = [];
  private lakeImages = new Map<number, Phaser.GameObjects.Image[]>(); // lake id -> its tiles
  private dry = "";
  private graves: { img: Phaser.GameObjects.Image; until: number }[] = [];
  private links: Link[] = [];
  private linkLayer!: Phaser.GameObjects.Graphics;
  private night!: Phaser.GameObjects.Rectangle;
  private ring!: Phaser.GameObjects.Image;
  private selected: number | null = null;
  private tickMs = 1000;
  private tick = 0;
  private userMovedCamera = false; // after the user pans/zooms/follows, stop auto-fitting
  ready = false; // frames are ignored until create() has finished
  showLinks = false;

  constructor(private hooks: SceneHooks) {
    super("world");
  }

  // ---------------------------------------------------------------- setup

  init(data: { snapshot: Snapshot }) {
    this.snap = data.snapshot;
  }

  create() {
    buildTerrain(this);
    buildThings(this);
    const { world } = this.snap;

    this.drawMap();
    world.food.forEach((f) => {
      this.bushes.push(this.add.image(f.x * T + 8, f.y * T + 8, "bush-full").setDepth(DEPTH.things));
      this.foodMax.push(f.max);
    });

    this.linkLayer = this.add.graphics().setDepth(DEPTH.links);
    this.ring = this.add.image(0, 0, "ring").setDepth(DEPTH.agents - 1).setVisible(false);
    this.night = this.add
      .rectangle(0, 0, world.width * T, world.height * T, 0x0a1030, 0)
      .setOrigin(0, 0)
      .setDepth(DEPTH.night);

    this.snap.agents.forEach((a) => this.addAgent(a));
    this.applyFrame(this.snap.frame, true);

    this.time.addEvent({
      delay: 450, loop: true,
      callback: () => {
        const f = Math.floor(this.time.now / 450) % 3;
        this.water.forEach((w) => w.setTexture(`water${f}`));
      },
    });

    this.setupCamera();
    this.applyUrlView();
    this.ready = true;
    this.hooks.onReady();
  }

  private drawMap() {
    const { world } = this.snap;
    const at = (x: number, y: number) => world.tiles[y]?.[x];
    const lakes = numberLakes(world.tiles, world.width, world.height);
    for (let y = 0; y < world.height; y++)
      for (let x = 0; x < world.width; x++) {
        const t = at(x, y);
        const px = x * T + 8, py = y * T + 8;
        const variant = (x * 7 + y * 13) % 4;
        if (t === "w") {
          const img = this.add.image(px, py, "water0").setDepth(DEPTH.ground);
          this.water.push(img);
          const lake = lakes.get(`${x},${y}`) ?? 0;
          if (!this.lakeImages.has(lake)) this.lakeImages.set(lake, []);
          this.lakeImages.get(lake)!.push(img);
          continue;
        }
        const mask = (at(x, y - 1) === "w" ? 1 : 0) | (at(x + 1, y) === "w" ? 2 : 0)
          | (at(x, y + 1) === "w" ? 4 : 0) | (at(x - 1, y) === "w" ? 8 : 0);
        const ground = mask ? shoreTexture(this, mask, variant) : `grass${variant}`;
        this.add.image(px, py, ground).setDepth(DEPTH.ground);
        if (t === "f") this.add.image(px, py, "tree").setDepth(DEPTH.things + y / 100);
        if (t === "m") this.add.image(px, py, "rock").setDepth(DEPTH.ground + 1);
        if (t === "k") { // a high ridge (M8e): rock on dark ground, impassable
          this.add.image(px, py, ground).setTint(0x55606e).setDepth(DEPTH.ground);
          this.add.image(px, py, "rock").setTint(0x8a8f99).setDepth(DEPTH.ground + 1);
        }
      }
  }

  private setupCamera() {
    const cam = this.cameras.main;
    cam.setBackgroundColor("#0b0f14");
    this.fitCamera();
    this.scale.on("resize", () => {
      if (!this.userMovedCamera) this.fitCamera();
    });

    let drag: { x: number; y: number; moved: boolean } | null = null;
    this.input.on("pointerdown", (p: Phaser.Input.Pointer) => {
      drag = { x: p.x, y: p.y, moved: false };
    });
    this.input.on("pointermove", (p: Phaser.Input.Pointer) => {
      if (!drag || !p.isDown) return;
      const dx = p.x - drag.x, dy = p.y - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      if (drag.moved) {
        this.userMovedCamera = true;
        cam.stopFollow();
        cam.scrollX -= dx / cam.zoom;
        cam.scrollY -= dy / cam.zoom;
        drag.x = p.x;
        drag.y = p.y;
      }
    });
    this.input.on("pointerup", (_p: Phaser.Input.Pointer, over: Phaser.GameObjects.GameObject[]) => {
      if (drag && !drag.moved && over.length === 0) this.select(null);
      drag = null;
    });
    this.input.on("wheel", (_p: unknown, _o: unknown, _dx: number, dy: number) => {
      this.userMovedCamera = true;
      cam.setZoom(Phaser.Math.Clamp(cam.zoom * (dy > 0 ? 0.85 : 1.18), 1, 10));
      this.updateLabelVisibility();
    });
  }

  /** ?follow=Name&zoom=5 in the address bar opens the viewer on someone. */
  private applyUrlView() {
    const params = new URLSearchParams(location.search);
    const name = params.get("follow")?.toLowerCase();
    const zoom = Number(params.get("zoom"));
    const match = [...this.agents.values()].find((a) => a.info.name.toLowerCase() === name);
    if (match) {
      this.follow(match.info.id);
      this.select(match.info.id);
    }
    if (zoom > 0) this.cameras.main.setZoom(Phaser.Math.Clamp(zoom, 1, 10));
    this.updateLabelVisibility();
  }

  fitCamera() {
    const { world } = this.snap;
    const cam = this.cameras.main;
    const zoom = Math.min(this.scale.width / (world.width * T), this.scale.height / (world.height * T));
    cam.setZoom(Math.max(1, zoom * 0.96));
    cam.centerOn((world.width * T) / 2, (world.height * T) / 2);
    this.updateLabelVisibility();
  }

  private updateLabelVisibility() {
    const show = this.cameras.main.zoom >= 2;
    this.agents.forEach((a) => a.label.setVisible(show && a.sprite.visible));
  }

  private addAgent(info: AgentInfo) {
    this.ensureSheet(info, "adult");
    const view = new AgentView(info, this);
    let lastClick = 0;
    view.sprite.on("pointerup", () => {
      const now = this.time.now;
      this.select(info.id);
      if (now - lastClick < 350) this.follow(info.id);
      lastClick = now;
    });
    this.agents.set(info.id, view);
  }

  /** Sprite sheet + walk animations for an agent at a life stage (made once, then reused). */
  private ensureSheet(info: AgentInfo, stage: string): string {
    const key = sheetKey(info.id, stage);
    if (this.textures.exists(key)) return key;
    characterTexture(this, key, info.looks, info.lineage, stage === "elder");
    for (const dir of ["down", "left", "right", "up"]) {
      this.anims.create({
        key: `walk-${key}-${dir}`,
        frames: [1, 0, 2, 0].map((i) => ({ key, frame: `${dir}-${i}` })),
        frameRate: 10,
        repeat: -1,
      });
    }
    return key;
  }

  addAgents(infos: AgentInfo[]) {
    infos.filter((a) => !this.agents.has(a.id)).forEach((a) => this.addAgent(a));
  }

  // ---------------------------------------------------------------- frames

  setSpeed(ticksPerSecond: number) {
    this.tickMs = ticksPerSecond > 0 ? 1000 / ticksPerSecond : 30;
  }

  applyFrame(frame: Frame, instant = false) {
    this.tick = frame.tick;
    if (frame.newAgents?.length) this.addAgents(frame.newAgents);
    const dry = (frame.dryLakes ?? []).join(",");
    if (dry !== this.dry) {  // a drought began or ended: dry lakes turn to cracked mud
      this.dry = dry;
      const dryIds = new Set(frame.dryLakes ?? []);
      this.lakeImages.forEach((imgs, id) =>
        imgs.forEach((img) => (dryIds.has(id) ? img.setTint(0x9c7b4f) : img.clearTint())));
    }
    for (const s of frame.agents) this.updateAgent(s, instant);
    frame.food.forEach((q, i) => {
      const ratio = q / this.foodMax[i];
      this.bushes[i]?.setTexture(ratio >= 0.5 ? "bush-full" : q >= 10 ? "bush-half" : "bush-bare");
    });
    this.links = frame.links;
    this.setNight(frame.hour);
    for (const e of frame.events) this.showEvent(e);
    this.graves = this.graves.filter((g) => {
      if (g.until > this.tick) return true;
      g.img.destroy();
      return false;
    });
  }

  private updateAgent(s: AgentState, instant: boolean) {
    const view = this.agents.get(s.id);
    if (!view) return;
    const prev = view.state;
    view.state = s;

    if (!s.alive) {
      view.moving?.stop();
      view.sprite.setPosition(s.x * T + 8, s.y * T + 6); // where they died (for follow/pan)
      view.sprite.setVisible(false);
      view.label.setVisible(false);
      view.icon.setVisible(false);
      view.bubble?.destroy();
      view.bubble = null;
      view.queue = [];
      return;
    }

    if (s.stage !== view.stage) {
      view.stage = s.stage;
      view.sheet = this.ensureSheet(view.info, s.stage);
      view.sprite.anims.stop();
      view.sprite.setTexture(view.sheet, `${s.facing}-0`);
    }
    const scale = s.stage === "child" ? 0.7 : 1;
    view.sprite.setScale(scale * (1 + (view.info.looks.strength - 0.5) * 0.12), scale);

    const tx = s.x * T + 8, ty = s.y * T + 6;
    const moved = !prev || prev.x !== s.x || prev.y !== s.y;
    view.moving?.stop();
    if (instant || !prev) {
      view.sprite.setPosition(tx, ty);
      view.sprite.setFrame(`${s.facing}-0`);
    } else if (moved) {
      const slow = s.stage === "elder" ? 1.25 : 1;
      view.sprite.anims.play({ key: `walk-${view.sheet}-${s.facing}`, frameRate: 10 / slow }, true);
      view.moving = this.tweens.add({
        targets: view.sprite, x: tx, y: ty,
        duration: Phaser.Math.Clamp(this.tickMs * 0.9, 40, 900),
        onComplete: () => {
          view.sprite.anims.stop();
          view.sprite.setFrame(`${s.facing}-0`);
        },
      });
    } else {
      view.sprite.anims.stop();
      view.sprite.setFrame(`${s.facing}-0`);
    }
    view.sprite.setAngle(s.icon === "sleep" ? 90 : 0);
    view.sprite.setAlpha(s.energy < 15 ? 0.85 : 1);

    if (s.group !== view.group) {
      view.group = s.group;
      view.label.setBackgroundColor(s.group ? GROUP_COLOURS[(s.group - 1) % GROUP_COLOURS.length] : "");
      view.label.setPadding(s.group ? 1 : 0, 0);
    }
    if (s.icon) view.icon.setTexture(`icon-${s.icon}`).setVisible(true);
    else view.icon.setVisible(false);
    view.label.setVisible(this.cameras.main.zoom >= 2);
  }

  private showEvent(e: EventItem) {
    if (e.bubble && e.actors.length) this.queueLine(e.actors[0], e.bubble, e.lie);
    if (e.float && e.actors.length) this.float(e.actors[0], e.float, FLOAT_COLOURS[e.type] ?? "#fff");
    if (e.type === "died" && e.x !== null && e.y !== null) {
      this.graves.push({
        img: this.add.image(e.x * T + 8, e.y * T + 8, "grave").setDepth(DEPTH.things),
        until: this.tick + GRAVE_TICKS,
      });
    }
  }

  /** Show a line now, or after the agent's current bubble (agents may speak twice a tick). */
  private queueLine(id: number, text: string, lie: boolean) {
    const view = this.agents.get(id);
    if (!view || !view.sprite.visible) return;
    if (view.bubble && view.queue.length < 3) view.queue.push({ text, lie });
    else this.say(id, text, lie);
  }

  private say(id: number, text: string, lie: boolean) {
    const view = this.agents.get(id);
    if (!view || !view.sprite.visible) return;
    view.bubble?.destroy();
    const label = this.add.text(0, 0, text, {
      fontFamily: "monospace", fontSize: "5px", color: "#111", resolution: 8,
      wordWrap: { width: 70 },
    }).setOrigin(0.5, 1);
    const w = label.width + 6, h = label.height + 4;
    const g = this.add.graphics();
    g.fillStyle(0xffffff, 0.95).fillRoundedRect(-w / 2, -h - 2, w, h, 2);
    g.lineStyle(0.6, lie ? 0xd63031 : 0x2d3436, 1).strokeRoundedRect(-w / 2, -h - 2, w, h, 2);
    g.fillStyle(0xffffff, 0.95).fillTriangle(-2, -2.2, 2, -2.2, 0, 1);
    label.setPosition(0, -4);
    view.bubble = this.add.container(view.x, view.y, [g, label]).setDepth(DEPTH.bubbles);
    // Long lines (the agents' own words) stay up longer, but never hold up the story.
    const readMs = 900 + 45 * text.length;
    const shown = Phaser.Math.Clamp(this.tickMs * 2.5, 900, 3500);
    view.bubbleUntil = this.time.now + Math.min(Math.max(shown, readMs), 6000) / (1 + view.queue.length);
    view.place();
  }

  /** Neighbours talking at once: lift overlapping bubbles so every line stays readable. */
  private unstackBubbles() {
    const placed: Phaser.Geom.Rectangle[] = [];
    const views = [...this.agents.values()].filter((a) => a.bubble).sort((a, b) => b.y - a.y);
    for (const a of views) {
      const bubble = a.bubble!;
      let box = bubble.getBounds();
      for (let tries = 0; tries < 6; tries++) {
        const hit = placed.find((p) => Phaser.Geom.Rectangle.Overlaps(p, box));
        if (!hit) break;
        bubble.y -= box.bottom - hit.top + 1;
        box = bubble.getBounds();
      }
      placed.push(box);
    }
  }

  private float(id: number, text: string, colour: string) {
    const view = this.agents.get(id);
    if (!view) return;
    const t = this.add.text(view.x, view.y - 14, text, {
      fontFamily: "monospace", fontSize: "5px", color: colour, stroke: "#000",
      strokeThickness: 1.5, resolution: 8,
    }).setOrigin(0.5).setDepth(DEPTH.bubbles).setScale(this.textScale());
    this.tweens.add({
      targets: t, y: t.y - 12, alpha: 0, duration: Phaser.Math.Clamp(this.tickMs * 3, 1200, 3000),
      onComplete: () => t.destroy(),
    });
  }

  private setNight(hour: number) {
    const alpha = [0.5, 0.5, 0.5, 0.5, 0.45, 0.35, 0.2, 0.08, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0.05, 0.12, 0.25, 0.35, 0.42, 0.48, 0.5][hour] ?? 0;
    this.tweens.add({ targets: this.night, fillAlpha: alpha, duration: 600 });
  }

  // ---------------------------------------------------------------- selection

  select(id: number | null) {
    this.selected = id;
    this.hooks.onSelect(id);
  }

  follow(id: number) {
    const view = this.agents.get(id);
    if (!view) return;
    this.userMovedCamera = true;
    if (!view.sprite.visible) {
      // The dead can't be followed: show where they died instead.
      this.cameras.main.stopFollow();
      this.cameras.main.centerOn(view.sprite.x, view.sprite.y);
      if (this.cameras.main.zoom < 4) this.cameras.main.setZoom(4);
      this.updateLabelVisibility();
      return;
    }
    this.cameras.main.startFollow(view.sprite, true, 0.15, 0.15);
    if (this.cameras.main.zoom < 4) this.cameras.main.setZoom(4);
    this.updateLabelVisibility();
  }

  panTo(x: number, y: number) {
    this.userMovedCamera = true;
    this.cameras.main.stopFollow();
    this.cameras.main.pan(x * T + 8, y * T + 8, 400, "Sine.easeInOut");
  }

  agentTextureKey(id: number) {
    return this.agents.get(id)?.sheet ?? sheetKey(id, "adult");
  }

  // ---------------------------------------------------------------- every frame

  /** Text stays readable when zoomed out: bubbles grow as the camera zooms out. */
  private textScale() {
    return Phaser.Math.Clamp(3 / this.cameras.main.zoom, 1, 3);
  }

  update() {
    const now = this.time.now;
    const textScale = this.textScale();
    this.agents.forEach((a) => {
      a.place();
      a.bubble?.setScale(textScale);
      if (a.bubble && now > a.bubbleUntil) {
        a.bubble.destroy();
        a.bubble = null;
        const next = a.queue.shift();
        if (next) this.say(a.info.id, next.text, next.lie);
      }
    });
    this.unstackBubbles();

    const sel = this.selected !== null ? this.agents.get(this.selected) : undefined;
    if (sel && sel.sprite.visible) this.ring.setPosition(sel.x, sel.y + 7).setVisible(true);
    else this.ring.setVisible(false);

    this.linkLayer.clear();
    if (!this.showLinks) return;
    for (const [from, to, label] of this.links) {
      const a = this.agents.get(from), b = this.agents.get(to);
      if (!a || !b) continue;
      // Each person's feeling is drawn on their half of the line, so one-sided feelings show.
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      this.linkLayer.lineStyle(["close friend", "enemy", "partner"].includes(label) ? 1.5 : 0.8,
                               LINK_COLOURS[label], 0.9);
      this.linkLayer.lineBetween(a.x, a.y, mx, my);
    }
  }
}
