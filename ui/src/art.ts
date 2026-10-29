// Pixel art drawn in code: terrain, bushes, graves, icons and gene-driven characters.
// Every sprite is 16×16 "art pixels"; the camera zoom makes them big and crisp.

import Phaser from "phaser";
import type { Looks } from "./protocol";

export const T = 16; // tile size in art pixels
export const DIRS = ["down", "left", "right", "up"] as const;

// ------------------------------------------------------------------ palettes (genes → colours)

const SKIN = ["#f6d5b8", "#eec39a", "#d9a066", "#b97a4a", "#8d5a35", "#5e3a22"];
const HAIR = ["#1c1410", "#3b2416", "#5c3a1f", "#8a4b23", "#b5651d", "#c9a227", "#e3cf7a", "#7a1f1f"];
const EYES = ["#3a6fd8", "#2f8f4e", "#6b4423", "#8b7a3a", "#6d7b8a"];
const CLOTHES = [
  "#c0392b", "#2980b9", "#27ae60", "#8e44ad", "#d35400", "#16a085",
  "#e1b12c", "#e84393", "#2c3e50", "#a0522d", "#00a8ff", "#6ab04c",
];
const ELDER_HAIR = "#d6d6d6";
const PANTS = "#4a3b2a";
const BOOTS = "#2b1d14";
const OUTLINE = "#161616";

const pick = (gene: number, list: string[]) =>
  list[Math.min(list.length - 1, Math.floor(gene * list.length))];

export function clothesColour(lineage: number): string {
  return CLOTHES[(lineage - 1 + CLOTHES.length) % CLOTHES.length];
}

// ------------------------------------------------------------------ helpers

type Grid = (string | null)[][]; // [y][x] colour or empty

function blank(w = T, h = T): Grid {
  return Array.from({ length: h }, () => Array<string | null>(w).fill(null));
}

function fromTemplate(rows: string[], colours: Record<string, string>): Grid {
  const g = blank();
  rows.forEach((row, y) =>
    [...row].forEach((ch, x) => {
      if (ch !== "." && colours[ch]) g[y][x] = colours[ch];
    }),
  );
  return g;
}

function mirror(g: Grid): Grid {
  return g.map((row) => [...row].reverse());
}

function outline(g: Grid, colour = OUTLINE): Grid {
  const out = g.map((row) => [...row]);
  for (let y = 0; y < g.length; y++)
    for (let x = 0; x < g[0].length; x++) {
      if (g[y][x]) continue;
      const n = [g[y - 1]?.[x], g[y + 1]?.[x], g[y]?.[x - 1], g[y]?.[x + 1]];
      if (n.some((c) => c && c !== colour)) out[y][x] = colour;
    }
  return out;
}

function paint(ctx: CanvasRenderingContext2D, g: Grid, ox = 0, oy = 0) {
  g.forEach((row, y) =>
    row.forEach((c, x) => {
      if (!c) return;
      ctx.fillStyle = c;
      ctx.fillRect(ox + x, oy + y, 1, 1);
    }),
  );
}

function rng(seed: number) {
  // mulberry32: small deterministic random, so tiles look the same every time
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function canvasTexture(scene: Phaser.Scene, key: string, w: number, h: number,
                       draw: (ctx: CanvasRenderingContext2D) => void) {
  if (scene.textures.exists(key)) return;
  const tex = scene.textures.createCanvas(key, w, h)!;
  draw(tex.getContext());
  tex.refresh();
}

// ------------------------------------------------------------------ terrain

const GRASS = "#5a9e3a";

function grass(ctx: CanvasRenderingContext2D, seed: number) {
  const r = rng(seed);
  ctx.fillStyle = GRASS;
  ctx.fillRect(0, 0, T, T);
  for (let i = 0; i < 22; i++) {
    ctx.fillStyle = r() < 0.5 ? "#4e8f32" : "#6db347";
    ctx.fillRect(Math.floor(r() * T), Math.floor(r() * T), 1, 1);
  }
  if (r() < 0.35) {
    ctx.fillStyle = r() < 0.5 ? "#f5f6fa" : "#fbc531";
    ctx.fillRect(Math.floor(r() * 14) + 1, Math.floor(r() * 14) + 1, 1, 1);
  }
}

export function buildTerrain(scene: Phaser.Scene) {
  for (let v = 0; v < 4; v++) canvasTexture(scene, `grass${v}`, T, T, (c) => grass(c, 11 + v));

  canvasTexture(scene, "tree", T, T, (c) => {
    grass(c, 99);
    const g = fromTemplate(
      [
        "................",
        "......gggg......",
        "....gggGgggg....",
        "...ggGGggggGg...",
        "..gggggggGgggg..",
        "..ggGgggggggGg..",
        "..gggggGggggggg.",
        "...ggggggggGgg..",
        "....gggggggggd..",
        ".....dggggggd...",
        "......ddttdd....",
        ".......tttt.....",
        ".......tttt.....",
        "......ttttt.....",
        "................",
        "................",
      ],
      { g: "#2f6b2a", G: "#47963a", d: "#1f4d1c", t: "#6b4423" },
    );
    paint(c, outline(g, "#13300f"));
  });

  canvasTexture(scene, "rock", T, T, (c) => {
    c.fillStyle = "#8a7f6d";
    c.fillRect(0, 0, T, T);
    const r = rng(7);
    for (let i = 0; i < 18; i++) {
      c.fillStyle = r() < 0.5 ? "#7b705f" : "#9a8e7a";
      c.fillRect(Math.floor(r() * T), Math.floor(r() * T), 1, 1);
    }
    const g = fromTemplate(
      [
        "................",
        "................",
        "......hhhh......",
        "....hhrrrrr.....",
        "...hrrrrrrrrs...",
        "..hrrrrrrrrrss..",
        "..rrrrrrrrrrss..",
        ".hrrrrrhhrrrrss.",
        ".rrrrrhrrrrrrss.",
        ".rrrrrrrrrrrsss.",
        "..rrrrrrrrrsss..",
        "...sssssssss....",
        "................",
        "................",
        "................",
        "................",
      ],
      { h: "#c4c4c4", r: "#9a9a9a", s: "#6f6f6f" },
    );
    paint(c, outline(g, "#3d3d3d"));
  });

  for (let f = 0; f < 3; f++)
    canvasTexture(scene, `water${f}`, T, T, (c) => {
      c.fillStyle = "#3a7bd5";
      c.fillRect(0, 0, T, T);
      const r = rng(5);
      for (let i = 0; i < 6; i++) {
        const x = Math.floor(r() * 12), y = Math.floor(r() * 15);
        c.fillStyle = "#6aa8f5";
        c.fillRect((x + f * 2) % 14, y, 3, 1);
        c.fillStyle = "#2e66b8";
        c.fillRect((x + 7 + f) % 14, (y + 5) % 15, 2, 1);
      }
    });
}

/** Grass with a sandy edge on the sides that touch water. mask bits: 1=N 2=E 4=S 8=W */
export function shoreTexture(scene: Phaser.Scene, mask: number, variant: number): string {
  const key = `shore${mask}-${variant}`;
  canvasTexture(scene, key, T, T, (c) => {
    grass(c, 11 + variant);
    c.fillStyle = "#d9c38c";
    if (mask & 1) c.fillRect(0, 0, T, 3);
    if (mask & 2) c.fillRect(T - 3, 0, 3, T);
    if (mask & 4) c.fillRect(0, T - 3, T, 3);
    if (mask & 8) c.fillRect(0, 0, 3, T);
    c.fillStyle = "#c2ab72";
    if (mask & 1) c.fillRect(0, 3, T, 1);
    if (mask & 2) c.fillRect(T - 4, 0, 1, T);
    if (mask & 4) c.fillRect(0, T - 4, T, 1);
    if (mask & 8) c.fillRect(3, 0, 1, T);
  });
  return key;
}

// ------------------------------------------------------------------ things

export function buildThings(scene: Phaser.Scene) {
  const bush = (berries: number, leaf: string, leafDark: string) => (c: CanvasRenderingContext2D) => {
    const g = fromTemplate(
      [
        "................",
        "................",
        "................",
        "................",
        ".....llll.......",
        "...lllLllll.....",
        "..llLllllllLl...",
        ".llllllLlllll...",
        ".lLllllllllLll..",
        ".lllllLlllllll..",
        "..llllllllLll...",
        "...ddllllldd....",
        ".....dddd.......",
        "................",
        "................",
        "................",
      ],
      { l: leaf, L: "#7bc35a", d: leafDark },
    );
    const r = rng(3);
    for (let i = 0; i < berries; i++) {
      const x = 2 + Math.floor(r() * 11), y = 5 + Math.floor(r() * 6);
      if (g[y][x]) g[y][x] = "#d63031";
    }
    paint(c, outline(g, "#1f3d14"), 0, 2);
  };
  canvasTexture(scene, "bush-full", T, T, bush(14, "#3e8e2f", "#2a6420"));
  canvasTexture(scene, "bush-half", T, T, bush(5, "#3e8e2f", "#2a6420"));
  canvasTexture(scene, "bush-bare", T, T, bush(0, "#6f7d3a", "#4d5728"));

  canvasTexture(scene, "grave", T, T, (c) => {
    const g = fromTemplate(
      [
        "................",
        "................",
        "................",
        "................",
        "................",
        ".......ss.......",
        ".......ss.......",
        ".....ssssss.....",
        ".....ssssss.....",
        ".......ss.......",
        ".......ss.......",
        ".......ss.......",
        ".....dddddd.....",
        "....dddddddd....",
        "................",
        "................",
      ],
      { s: "#b8b8b8", d: "#6b5a3e" },
    );
    paint(c, outline(g));
  });

  canvasTexture(scene, "ring", T, 6, (c) => {
    c.strokeStyle = "#ffe066";
    c.lineWidth = 1;
    c.beginPath();
    c.ellipse(8, 3, 6, 2, 0, 0, Math.PI * 2);
    c.stroke();
  });

  const icon = (key: string, rows: string[], colours: Record<string, string>) =>
    canvasTexture(scene, `icon-${key}`, 8, 8, (c) => {
      const g: Grid = Array.from({ length: 8 }, () => Array<string | null>(8).fill(null));
      rows.forEach((row, y) => [...row].forEach((ch, x) => {
        if (ch !== ".") g[y][x] = colours[ch];
      }));
      paint(c, g);
    });
  icon("thirsty", ["...b....", "..bbb...", ".bbwbb..", ".bwbbb..", "bbbbbbb.", "bbbbbbb.", ".bbbbb..", "..bbb..."],
       { b: "#4aa3ff", w: "#e8f4ff" });
  icon("hungry", ["....g...", "...g....", ".rr.rr..", "rrrrrrr.", "rwrrrrr.", "rrrrrrr.", ".rrrrr..", "..rrr..."],
       { r: "#e74c3c", w: "#ffb3a7", g: "#27ae60" });
  icon("sleep", ["....zzzz", "......z.", ".....z..", "zzz.zzzz", "..z.....", ".z......", "zzz.....", "........"],
       { z: "#f5f6fa" });
  icon("talk", [".wwwwww.", "wwwwwwww", "wkwwkwkw", "wwwwwwww", ".wwwwww.", "..ww....", ".w......", "........"],
       { w: "#ffffff", k: "#2d3436" });
  icon("search", [".kkkk...", "kwwwwk..", "kwwwwk..", "kwwwwk..", ".kkkkk..", ".....kk.", "......kk", "........"],
       { k: "#2d3436", w: "#c7ecee" });
  icon("angry", ["r..rr..r", ".r.rr.r.", "..r..r..", "rr....rr", "rr....rr", "..r..r..", ".r.rr.r.", "r..rr..r"],
       { r: "#ff4757" });
}

// ------------------------------------------------------------------ characters

const BODY_DOWN = [
  "................",
  ".....HHHHHH.....",
  "....HHHHHHHH....",
  "....HSSSSSSH....",
  "....SSESSESS....",
  "....SSSSSSSS....",
  ".....SSSSSS.....",
  "......CCCC......",
  "....CCCCCCCC....",
  "...SCCCCCCCCS...",
  "...SCCCCCCCCS...",
  "....cCCCCCCc....",
  "....PPPPPPPP....",
];
const BODY_UP = [
  "................",
  ".....HHHHHH.....",
  "....HHHHHHHH....",
  "....HHHHHHHH....",
  "....HHHHHHHH....",
  "....HHHHHHHH....",
  ".....HHHHHH.....",
  "......SSSS......",
  "....CCCCCCCC....",
  "...SCCCCCCCCS...",
  "...SCCCCCCCCS...",
  "....cCCCCCCc....",
  "....PPPPPPPP....",
];
const BODY_LEFT = [
  "................",
  "......HHHHH.....",
  ".....HHHHHHH....",
  ".....SSSHHHH....",
  "....SESSSHHH....",
  "....SSSSSSHH....",
  ".....SSSSSS.....",
  "......CCCC......",
  ".....CCCCCC.....",
  ".....CSCCCC.....",
  ".....CSCCCC.....",
  ".....cCCCCc.....",
  ".....PPPPPP.....",
];
const LEGS_FRONT = {
  idle: ["....PPP..PPP....", "....PPP..PPP....", "....BBB..BBB...."],
  a: ["....PPP..PPP....", "....PPP..BBB....", "....BBB........."],
  b: ["....PPP..PPP....", "....BBB..PPP....", ".........BBB...."],
};
const LEGS_SIDE = {
  idle: [".....PP..PP.....", ".....PP..PP.....", ".....BB..BB....."],
  a: ["....PP....PP....", "....PP....PP....", "...BB......BB..."],
  b: [".....PPPPPP.....", "......PPPP......", "......BBBB......"],
};

/** Builds a 3-frame × 4-direction sheet for one agent. Frames: `${dir}-${0|1|2}`. */
export function characterTexture(scene: Phaser.Scene, key: string, looks: Looks, lineage: number,
                                 elder: boolean) {
  if (scene.textures.exists(key)) return; // sheets are never replaced while in use
  const colours = {
    H: elder ? ELDER_HAIR : pick(looks.hair, HAIR),
    S: pick(looks.skin, SKIN),
    E: pick(looks.eyes, EYES),
    C: clothesColour(lineage),
    c: Phaser.Display.Color.HexStringToColor(clothesColour(lineage)).darken(25).rgba,
    P: PANTS,
    B: BOOTS,
  };
  const tex = scene.textures.createCanvas(key, T * 3, T * 4)!;
  const ctx = tex.getContext();
  const frames = ["idle", "a", "b"] as const;

  DIRS.forEach((dir, row) => {
    frames.forEach((frame, col) => {
      const side = dir === "left" || dir === "right";
      const body = dir === "down" ? BODY_DOWN : dir === "up" ? BODY_UP : BODY_LEFT;
      const legs = (side ? LEGS_SIDE : LEGS_FRONT)[frame];
      let g = outline(fromTemplate([...body, ...legs], colours));
      if (dir === "right") g = mirror(g);
      paint(ctx, g, col * T, row * T);
      tex.add(`${dir}-${col}`, 0, col * T, row * T, T, T);
    });
  });
  tex.refresh();
}

/** Draws one agent facing the viewer, scaled up, onto a normal canvas (inspect portrait). */
export function portrait(scene: Phaser.Scene, key: string, canvas: HTMLCanvasElement, scale = 6) {
  const tex = scene.textures.get(key);
  const src = tex.getSourceImage() as HTMLCanvasElement;
  canvas.width = T * scale;
  canvas.height = T * scale;
  const c = canvas.getContext("2d")!;
  c.imageSmoothingEnabled = false;
  c.clearRect(0, 0, canvas.width, canvas.height);
  c.drawImage(src, 0, 0, T, T, 0, 0, T * scale, T * scale);
}
