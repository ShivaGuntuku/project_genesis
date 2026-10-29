"""Interview mode (M10): ask someone a question, as they were in a chosen year.

The answer is built only from that person's own memories up to the end of that year (what
they had not yet forgotten), their beliefs and their story as told then. It goes to the LLM
and comes back to the dashboard. Nothing is written to the world: the world never knows.
"""

import json
import os
import sqlite3

from fastapi import HTTPException

from genesis.culture.words import leak_words
from genesis.llm import gateway as gw
from genesis.llm import prompts

SCHEMA = {
    "type": "object",
    "properties": {"thought": {"type": "string"}, "answer": {"type": "string"}},
    "required": ["thought", "answer"],
}


def ask(world, agent_id: int, question: str, year=None) -> dict:
    cal = world.calendar
    conn = sqlite3.connect(f"file:{world.path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        a = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if a is None:
            raise HTTPException(404, "No such person")
        now = int(conn.execute("SELECT value FROM meta WHERE key = 'tick'").fetchone()[0])
        last = a["died_tick"] if a["died_tick"] is not None else now
        year = int(year) if year else cal.year(last)
        limit = min(last, cal.ticks(days=year * cal.days_per_year) - 1)
        if limit < max(a["born_tick"], 0):
            raise HTTPException(400, f"{a['name']} was not yet born in year {year}")
        memories = list(
            conn.execute(
                "SELECT tick, kind, text, importance FROM memories WHERE agent_id = ? "
                "AND tick <= ? AND (forgotten_tick IS NULL OR forgotten_tick > ?) "
                "ORDER BY importance DESC, tick DESC LIMIT 14",
                (agent_id, limit, limit),
            )
        )
        story = conn.execute(
            "SELECT e.data_json FROM events e JOIN event_agents ea ON ea.event_id = e.id "
            "WHERE ea.agent_id = ? AND ea.role = 'actor' AND e.type = 'reflected' "
            "AND e.tick <= ? ORDER BY e.id DESC LIMIT 1",
            (agent_id, limit),
        ).fetchone()
        state = json.loads(a["state_json"])
    finally:
        conn.close()

    age = cal.age_days(limit - a["born_tick"]) // cal.days_per_year if cal.watches else None
    beliefs = [m["text"] for m in memories if m["kind"] == "belief"]
    remembered = [m for m in memories if m["kind"] != "belief"]
    lines = [
        f"You are {a['name']}"
        + (f", {age} years old" if age is not None else "")
        + f". It is {cal.when(limit)}.",
    ]
    if state.get("talent"):
        lines.append(f"Your gift: {state['talent']}.")
    if story:
        told = json.loads(story[0]).get("story")
        if told:
            lines.append(f"Your story, in your own words: {told}")
    if beliefs:
        lines.append("You believe: " + "; ".join(beliefs[:5]) + ".")
    lines.append("What you remember:")
    lines += [f"- {cal.when(m['tick'])}: {m['text']}" for m in remembered] or ["- very little"]
    lines += [
        "",
        f'Someone you cannot see asks you: "{question}"',
        "Answer as yourself, only from what you remember. If you do not know, say so.",
        'Answer in JSON: "thought" (private, under 15 words) and "answer" (under 60 words).',
    ]
    prompt = "\n".join(lines)

    config = world.config.llm
    if os.environ.get("GENESIS_FAKE_LLM"):
        from genesis.llm.fake import FakeGateway

        gateway = FakeGateway()
    else:
        gateway = gw.make_gateway(config, os.environ.get("GENESIS_OLLAMA_URL", config.url))
    reply = gateway.chat(
        config.chat_model, prompts.SYSTEM, prompt, SCHEMA, temperature=0.7, max_tokens=200
    )
    if reply.outcome != gw.OK:
        raise HTTPException(503, f"The LLM did not answer: {reply.error or reply.outcome}")
    try:
        data = json.loads(reply.text)
        answer = " ".join(str(data.get("answer", "")).split())
        thought = " ".join(str(data.get("thought", "")).split())
    except (ValueError, AttributeError):
        raise HTTPException(502, "The LLM's answer could not be read") from None
    leaked = leak_words(answer)
    return {
        "name": a["name"],
        "year": year,
        "asOf": cal.when(limit),
        "question": question,
        "answer": None if leaked else answer,
        "thought": thought,
        "leaked": leaked,
        "memoriesUsed": len(remembered),
        "beliefsUsed": len(beliefs),
        "prompt": prompt,
    }
