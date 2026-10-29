"""Model benchmark: real Genesis decision prompts against candidate models.

    genesis bench-llm --models llama3,llama3.2:3b --situations 50

Situations come from a rules-only run of a fresh world (the prompts are built exactly as in
a live run, but nobody answers them), picked evenly across decision kinds. Each model gets
the same situations. The report: valid answers, latency, choices, and samples to read.
"""

import random
import re
import statistics
from dataclasses import dataclass, field

from genesis.llm import gateway as gw
from genesis.llm import prompts, schemas
from genesis.llm.mind import PromptCollector
from genesis.simulation.engine import Simulation

# Words from the world outside the valley. A rough leak check (the full detector is M8).
EARTH_WORDS = re.compile(
    r"\b(money|coins?|city|cities|town|king|queen|god|gods|church|phone|car|internet|"
    r"computer|robot|school|book|doctor|police|dollars?|shop|market|simulation)\b",
    re.IGNORECASE,
)


@dataclass
class ModelResult:
    model: str
    valid: int = 0
    total: int = 0
    latencies: list[int] = field(default_factory=list)
    leaks: int = 0
    errors: dict = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)  # (kind, choice, say, thought)
    choices: dict = field(default_factory=dict)  # "request:give" -> count

    @property
    def valid_pct(self) -> float:
        return 100 * self.valid / self.total if self.total else 0.0

    def latency(self, q: float) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def collect_situations(n: int, seed: int = 7, founders: int = 8, max_days: int = 40) -> list:
    """Up to n decision prompts from a rules-only run, spread evenly over decision kinds."""
    sim = Simulation.create(seed=seed, founders=founders)
    collector = PromptCollector(sim.config.start_hour)
    sim.mind = collector
    for _ in range(max_days):
        sim.run(24)
        if len(collector.prompts) >= n * 6 or not sim.population.get_alive():
            break

    by_kind: dict[str, list] = {}
    for p in collector.prompts:
        by_kind.setdefault(p["kind"], []).append(p)
    rng = random.Random(seed)
    for items in by_kind.values():
        rng.shuffle(items)

    picked = []
    while len(picked) < n and any(by_kind.values()):
        for kind in sorted(by_kind):
            if by_kind[kind] and len(picked) < n:
                picked.append(by_kind[kind].pop())
    return picked


def run(models: list[str], situations: list, gateway, temperature=0.8, on_progress=None):
    results = []
    for model in models:
        result = ModelResult(model)
        # Warm-up (model load) is not counted.
        gateway.chat(model, prompts.SYSTEM, "Say hello.", schemas.schema(None), max_tokens=20)
        for i, s in enumerate(situations):
            reply = gateway.chat(
                model,
                prompts.SYSTEM,
                s["prompt"],
                s.get("schema") or schemas.schema(s["keys"]),
                temperature=temperature,
                seed=i,
                max_tokens=300 if s["kind"] == "reflect" else 150,
            )
            result.total += 1
            if reply.outcome != gw.OK:
                result.errors[reply.outcome] = result.errors.get(reply.outcome, 0) + 1
            else:
                sample = _check(s, reply.text)
                if sample is None:
                    result.errors["invalid"] = result.errors.get("invalid", 0) + 1
                else:
                    result.valid += 1
                    result.latencies.append(reply.latency_ms)
                    if EARTH_WORDS.search(f"{sample['say']} {sample['thought']}"):
                        result.leaks += 1
                    if sample["choice"] and s["kind"] != "reflect":
                        key = f"{s['kind']}:{sample['choice'].split(':')[0]}"
                        result.choices[key] = result.choices.get(key, 0) + 1
                    result.samples.append(sample)
            if on_progress:
                on_progress(model, i + 1, len(situations))
        results.append(result)
    return results


def _check(situation, text) -> dict | None:
    """Parse an answer exactly as the simulation would; a sample to read, or None."""
    if situation["kind"] == "reflect":
        from genesis.llm import reflection

        props = situation["schema"]["properties"]
        names = props.get("feelings", {}).get("items", {}).get("properties", {})
        names = names.get("name", {}).get("enum", [])
        r = reflection.parse(text, names, None)
        if r is None:
            return None
        return {
            "kind": "reflect",
            "choice": None,
            "say": " | ".join(r.beliefs) + (f"  STORY: {r.story}" if r.story else ""),
            "thought": r.thought,
        }
    try:
        answer = schemas.parse(text, situation["keys"])
    except schemas.InvalidAnswer:
        return None
    return {
        "kind": situation["kind"],
        "choice": answer.choice,
        "say": answer.say,
        "thought": answer.thought,
    }


def mean(values) -> float:
    return statistics.fmean(values) if values else 0.0


def throughput(model: str, situations: list, gateway, workers: int, temperature=0.8) -> float:
    """Calls per minute the machine answers with `workers` requests at once (M8.5)."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    def one(s):
        return gateway.chat(
            model,
            prompts.SYSTEM,
            s["prompt"],
            s["schema"],
            temperature=temperature,
            max_tokens=s.get("max_tokens", 150),
        )

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, situations))
    elapsed = time.monotonic() - started
    return 60 * len(situations) / elapsed if elapsed else 0.0
