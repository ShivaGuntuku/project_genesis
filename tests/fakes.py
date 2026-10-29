"""A fake local model for tests: answers chats (decisions and reflections) and embeds text."""

import json
import random
import re
import zlib

import numpy as np

from genesis.config import WorldConfig
from genesis.llm import gateway as gw
from genesis.llm.gateway import Reply
from genesis.llm.mind import attach
from genesis.llm.settings import LLMConfig
from genesis.simulation.engine import Simulation


class FakeModel:
    """Answers like Ollama would. `prefer` picks the first preferred key on offer;
    otherwise a choice is drawn from the prompt's hash (varied, but repeatable)."""

    def __init__(
        self,
        prefer=(),
        broken=False,
        feel="warmer",
        can_embed=True,
        child_name=None,
        goal_need=None,
    ):
        self.child_name = child_name  # fixed answer when asked to name a child
        self.goal_need = goal_need  # the need a reflection's goal is about (default: first)
        self.prefer = prefer
        self.broken = broken
        self.feel = feel  # what reflections answer about the first person they know
        self.can_embed = can_embed
        self.calls = []
        self.embedded = 0

    def available(self):
        return True

    def check(self):
        return True, ["llama3:latest", "nomic-embed-text:latest"]

    def chat(self, model, system, prompt, schema, temperature=0.8, seed=None, max_tokens=150):
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        if self.broken:
            return Reply(gw.OK, text='{"thought": "hmm", "choice": "fly away", "say": "Bye"}')
        props = schema["properties"]
        if "beliefs" in props:  # a nightly reflection
            names = props.get("feelings", {}).get("items", {}).get("properties", {})
            names = names.get("name", {}).get("enum", [])
            answer = {
                "thought": "A long day.",
                "beliefs": [f"Belief number {len(self.calls)}: the lake gives life."],
                "story": f"I live in the valley. Night {len(self.calls)} of thinking.",
                "feelings": [{"name": names[0], "change": self.feel}] if names else [],
            }
            needs = props.get("goal_need", {}).get("enum")
            if needs:  # M8c: a goal in its own words
                need = self.goal_need if self.goal_need in needs else needs[0]
                answer["goal"] = f"In my own words: I will see to {need}."
                answer["goal_need"] = need
            return Reply(gw.OK, text=json.dumps(answer), latency_ms=5)

        if "materials" in props:  # an idea for something to make (M8b)
            mats = props["materials"]["items"]["enum"]
            answer = {
                "thought": "Maybe this.",
                "idea": "a hollow thing to keep things in",
                "materials": mats[:2],
                "actions": ["shape", "hollow"],
            }
            return Reply(gw.OK, text=json.dumps(answer), latency_ms=5)

        if "name" in props and "choice" not in props:  # naming a newborn (or an invention)
            answer = {
                "thought": "A good name.",
                "name": self.child_name or f"Zor{chr(97 + len(self.calls) % 26)}in",
                "say": "Let's call our child this.",
            }
            return Reply(gw.OK, text=json.dumps(answer), latency_ms=5)

        keys = props.get("choice", {}).get("enum")
        answer = {"thought": "It feels right.", "say": f"Line {len(self.calls)}, said warmly."}
        if keys:
            preferred = [k for p in self.prefer for k in keys if k.startswith(p)]
            pick = random.Random(zlib.crc32(prompt.encode())).choice(keys)
            answer["choice"] = preferred[0] if preferred else pick
        return Reply(gw.OK, text=json.dumps(answer), latency_ms=5)

    def embed(self, model, texts):
        """Words hashed into 64 dimensions: texts sharing words point the same way."""
        if not self.can_embed:
            return None
        self.embedded += len(texts)
        out = []
        for text in texts:
            v = np.zeros(64)
            for word in re.findall(r"[a-z]+", text.lower().split(":", 1)[-1]):
                v[zlib.crc32(word.encode()) % 64] += 1
            out.append((v + 0.01).tolist())
        return out


def llm_sim(seed=3, founders=5, model=None, **llm):
    config = WorldConfig.default().with_overrides(seed=seed, founders=founders)
    config = config.with_overrides(llm=LLMConfig(enabled=True, **llm))
    sim = Simulation(config)
    attach(sim, gateway=model or FakeModel())
    return sim
