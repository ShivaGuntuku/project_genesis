"""A stand-in model for checks and demos (GENESIS_FAKE_LLM=1): answers instantly, from the
prompt alone (the same prompt always gets the same answer, in any order or thread)."""

import json
import zlib

from genesis.llm import gateway as gw
from genesis.llm.gateway import Reply


class FakeGateway:
    def available(self):
        return True

    def check(self):
        return True, ["llama3:latest", "nomic-embed-text:latest"]

    def embed(self, model, texts):
        return None  # memory search falls back to shared words

    def chat(self, model, system, prompt, schema, temperature=0.8, seed=None, max_tokens=150):
        h = zlib.crc32(f"{seed}:{prompt}".encode())
        props = schema.get("properties", {})
        answer: dict = {"thought": "So it goes."}
        for key, spec in props.items():
            if key == "thought":
                continue
            enum = spec.get("enum") or spec.get("items", {}).get("enum")
            if spec.get("type") == "array":
                if enum:
                    answer[key] = enum[: 1 + h % min(2, len(enum))]
                elif key == "beliefs":
                    answer[key] = [f"Day by day the lake gives us what we need ({h % 97})"]
                else:
                    answer[key] = []
            elif enum:
                answer[key] = enum[h % len(enum)]
            elif key == "name":
                answer[key] = "Vel" + "aeiou"[h % 5] + "rin"[h % 3] + "kt"[h % 2]
            else:
                answer[key] = f"By the lake, as the reeds sway ({h % 89})."
        return Reply(gw.OK, text=json.dumps(answer), latency_ms=1)
