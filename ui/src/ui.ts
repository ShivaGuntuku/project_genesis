// HTML around the game: top bar, inspect panel, event feed, "why?" window.

import type { AgentDetail, EventItem, WhyResult } from "./protocol";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

export function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

const LABEL_CLASS: Record<string, string> = {
  friend: "good", "close friend": "good strong", rival: "warn", enemy: "bad", stranger: "dim",
};

function label(text: string | null) {
  if (!text) return '<span class="dim">—</span>';
  return `<span class="tag ${LABEL_CLASS[text] ?? ""}">${esc(text)}</span>`;
}

function bar(name: string, value: number, goodHigh: boolean) {
  const bad = goodHigh ? value < 30 : value > 70;
  const warn = goodHigh ? value < 60 : value > 50;
  const cls = bad ? "bad" : warn ? "warn" : "good";
  return `<div class="bar"><span>${name}</span><div><i class="${cls}" style="width:${value}%"></i></div><b>${value}</b></div>`;
}

function eventLine(e: EventItem, whyButton = true) {
  const why = whyButton && e.hasCause ? ` <button class="why" data-event="${e.id}">why?</button>` : "";
  const lie = e.lie ? ' <span class="tag bad">LIE</span>' : "";
  const thought = e.thought ? ` <span class="thought">(thinks: ${esc(e.thought)})</span>` : "";
  const text = esc(e.text.replace(" [LIE]", ""));
  return `<span class="when">${esc(e.clock)}</span> ${e.voiced ? `<span class="voiced">${text}</span>` : text}${lie}${thought}${why}`;
}

// ------------------------------------------------------------------ top bar

export interface BarHandlers {
  onPlayPause(): void;
  onSpeed(speed: number): void;
  onLinks(show: boolean): void;
  onFind(name: string): void;
}

export function setupTopBar(h: BarHandlers) {
  $("play").addEventListener("click", h.onPlayPause);
  document.querySelectorAll<HTMLButtonElement>("#speeds button").forEach((b) =>
    b.addEventListener("click", () => h.onSpeed(Number(b.dataset.speed))),
  );
  $<HTMLInputElement>("links").addEventListener("change", (e) =>
    h.onLinks((e.target as HTMLInputElement).checked),
  );
  $<HTMLInputElement>("search").addEventListener("change", (e) => {
    const input = e.target as HTMLInputElement;
    h.onFind(input.value.trim());
    input.value = "";
  });
  document.addEventListener("keydown", (e) => {
    if ((e.target as HTMLElement).tagName === "INPUT") return;
    if (e.code === "Space") {
      e.preventDefault();
      h.onPlayPause();
    }
  });
}

export function setNames(names: string[]) {
  $("names").innerHTML = names.map((n) => `<option value="${esc(n)}">`).join("");
}

export function setStatus(paused: boolean, speed: number) {
  $("play").textContent = paused ? "▶ Play" : "⏸ Pause";
  document.querySelectorAll<HTMLButtonElement>("#speeds button").forEach((b) =>
    b.classList.toggle("on", Number(b.dataset.speed) === speed),
  );
}

export function setClock(world: string, clock: string, alive: number) {
  $("world-name").textContent = world;
  $("clock").textContent = clock;
  $("alive").textContent = `${alive} alive`;
}

export function toast(message: string) {
  const t = $("toast");
  t.textContent = message;
  t.hidden = false;
  setTimeout(() => (t.hidden = true), 4000);
}

// ------------------------------------------------------------------ event feed

const MAX_FEED = 120;

export function setupFeed(onPan: (x: number, y: number) => void, onWhy: (id: number) => void) {
  const list = $("events");
  list.addEventListener("click", (e) => {
    const target = e.target as HTMLElement;
    const why = target.closest<HTMLButtonElement>("button.why");
    if (why) {
      onWhy(Number(why.dataset.event));
      return;
    }
    const li = target.closest<HTMLLIElement>("li");
    if (li?.dataset.x) onPan(Number(li.dataset.x), Number(li.dataset.y));
  });
  $<HTMLInputElement>("routine").addEventListener("change", (e) => {
    list.classList.toggle("show-routine", (e.target as HTMLInputElement).checked);
  });
}

export function addEvents(events: EventItem[]) {
  const list = $("events");
  for (const e of events) {
    const li = document.createElement("li");
    li.className = `${e.routine ? "routine" : ""} type-${e.type}`;
    if (e.x !== null) {
      li.dataset.x = String(e.x);
      li.dataset.y = String(e.y);
    }
    li.innerHTML = eventLine(e);
    list.prepend(li);
  }
  while (list.children.length > MAX_FEED) list.lastElementChild?.remove();
}

// ------------------------------------------------------------------ inspect panel

export function setupPanel(onClose: () => void, onWhy: (id: number) => void,
                           onPick: (id: number) => void) {
  const panel = $("panel");
  panel.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    if (t.closest("#panel-close")) onClose();
    const why = t.closest<HTMLButtonElement>("button.why");
    if (why) onWhy(Number(why.dataset.event));
    const person = t.closest<HTMLElement>("[data-agent]");
    if (person) onPick(Number(person.dataset.agent));
  });
}

export function hidePanel() {
  $("panel").hidden = true;
}

export function showPanel(d: AgentDetail, drawPortrait: (canvas: HTMLCanvasElement) => void) {
  const panel = $("panel");
  const status = d.alive
    ? `${d.stage}, ${d.ageDays} days old <span class="dim">(lifespan ~${d.lifespanDays})</span>`
    : `<span class="tag bad">died ${esc(d.died ?? "")} of ${esc(d.causeOfDeath ?? "")}</span>`;
  const list = (items: string[], empty: string) =>
    items.length ? items.map(esc).join(", ") : `<span class="dim">${empty}</span>`;

  const relations = d.relationships.map((r) => `
      <tr data-agent="${r.id}" class="${r.alive ? "" : "dead"}">
        <td class="name">${esc(r.name)}${r.alive ? "" : " †"}${r.met ? "" : ' <span class="dim">(heard of)</span>'}</td>
        <td>${label(r.label)}</td><td>${label(r.theirLabel)}</td>
        <td class="num">${r.affinity >= 0 ? "+" : ""}${r.affinity.toFixed(2)}</td>
        <td class="num">${r.trust.toFixed(2)}</td>
      </tr>`).join("");

  const genes = Object.entries(d.genes).map(([g, v]) =>
    `<div class="gene"><span>${g}</span><div><i style="width:${v * 100}%"></i></div></div>`).join("");

  panel.innerHTML = `
    <button id="panel-close" title="Close">✕</button>
    <div class="who">
      <canvas id="portrait"></canvas>
      <div><h2>${esc(d.name)}</h2><div>${status}</div>
        <div class="dim">generation ${d.generation} · at (${d.position[0]},${d.position[1]}) · ${esc(d.action ?? "—")}</div></div>
    </div>
    ${d.alive ? `<section>${bar("health", d.needs.health, true)}${bar("energy", d.needs.energy, true)}
      ${bar("hunger", d.needs.hunger, false)}${bar("thirst", d.needs.thirst, false)}</section>` : ""}
    <section class="facts">
      <div><b>Character</b> ${list(d.character, "balanced")}</div>
      <div><b>Strengths</b> ${list(d.strengths, "none notable")}</div>
      <div><b>Weaknesses</b> ${list(d.weaknesses, "none notable")}</div>
      <div><b>Skills</b> ${list(d.skills, "none yet")}</div>
      <div><b>Carrying</b> ${d.carrying.food} food, ${d.carrying.water} water</div>
      <div><b>Knows</b> ${d.knowsFood} food patch(es)${d.knowsWater ? ", where water is" : ", no water yet"} · sees ${d.vision} tiles</div>
    </section>
    ${d.group ? `<section><h3>Group</h3><div><b>${esc(d.group.name)}</b> · ${d.group.members} members · led by
      ${d.group.leaderId === d.id ? "them" : `<span class="kin" data-agent="${d.group.leaderId}">${esc(d.group.leader ?? "?")}</span>`}
      ${d.group.order ? ` · <span class="tag">ordered: ${esc(d.group.order)}</span>` : ""}</div></section>` : ""}
    ${d.family.length || d.expecting ? `<section><h3>Family</h3>${d.expecting ? '<div class="tag">expecting a child</div>' : ""}
      <div class="family">${d.family.map((f) => `<span class="kin${f.alive ? "" : " dead"}" data-agent="${f.id}">${esc(f.name)} <i>${esc(f.relation)}</i></span>`).join("")}</div></section>` : ""}
    <section><h3>Mind</h3>
      ${d.story ? `<p class="story">“${esc(d.story)}”</p>` : '<div class="dim">No reflection yet (needs the LLM mind).</div>'}
      ${d.talent ? `<div>Talent: <b>${esc(d.talent)}</b>${d.profession ? ` · known as a ${esc(d.profession)}` : ""}</div>` : ""}
      ${d.goal ? `<div>🎯 <b>“${esc(d.goal.text)}”</b> <span class="dim">(${esc(d.goal.need)}, since ${esc(d.goal.since)})</span></div>` : ""}
      ${d.drives ? `<div class="dim">Drives: ${Object.entries(d.drives).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(" · ")}</div>` : ""}
      ${d.beliefs.length ? `<ul class="beliefs">${d.beliefs.map((b) => `<li>${esc(b.text)} <span class="when">${esc(b.clock)}</span></li>`).join("")}</ul>` : ""}
      ${d.memories.length ? `<details><summary>What weighs most (${d.memories.length})</summary><ul class="life">${d.memories.map((m) => `<li><span class="when">${esc(m.clock)}</span> [${m.importance}] ${esc(m.text)}</li>`).join("")}</ul></details>` : ""}
    </section>
    <section><h3>Relationships</h3>${d.relationships.length ? `
      <table class="rel"><tr><th></th><th>I see them as</th><th>they see me as</th><th>affinity</th><th>trust</th></tr>${relations}</table>`
      : '<div class="dim">Knows nobody yet.</div>'}</section>
    <section><h3>Recent life</h3><ul class="life">${d.events.map((e) => `<li>${eventLine(e)}</li>`).join("")}</ul></section>
    <details><summary>Genes</summary><div class="genes">${genes}</div></details>`;
  panel.hidden = false;
  drawPortrait($<HTMLCanvasElement>("portrait"));
}

// ------------------------------------------------------------------ why window

export function showWhy(w: WhyResult, onPan: (x: number, y: number) => void) {
  const modal = $("why");
  const chain = w.chain.map((e, i) =>
    `<li class="${i === w.chain.length - 1 ? "this" : ""}" data-x="${e.x ?? ""}" data-y="${e.y ?? ""}">${eventLine(e, false)}</li>`,
  ).join('<li class="arrow">↓ led to</li>');
  const after = w.consequences.length
    ? `<h3>It led to</h3><ul>${w.consequences.map((e) => `<li>${eventLine(e, false)}</li>`).join("")}</ul>` : "";
  modal.innerHTML = `<div class="box"><button class="close">✕</button>
    <h2>Why did #${w.event} happen?</h2><ul class="chain">${chain}</ul>
    ${w.chain.length === 1 ? '<p class="dim">No recorded cause: this started its own chain.</p>' : ""}${after}</div>`;
  modal.hidden = false;
  modal.onclick = (e) => {
    const t = e.target as HTMLElement;
    if (t === modal || t.closest(".close")) modal.hidden = true;
    const li = t.closest<HTMLLIElement>("li[data-x]");
    if (li && li.dataset.x) {
      onPan(Number(li.dataset.x), Number(li.dataset.y));
      modal.hidden = true;
    }
  };
}
