"""Memory search by meaning: embeddings from a small local model (nomic-embed-text).

Vectors are a cache derived from memory text. They live in `sim.memory_vectors` and are
saved in `memory_vectors`, but they never change history: they only decide which memories
are put in a prompt, and prompts are recorded with their answers.

If the model is missing or fails, `relevance` returns None and retrieval falls back to
shared words, so the world never waits on embeddings.
"""

import time

import numpy as np

BATCH = 64
RETRY_AFTER = 60.0  # seconds to wait after a failure before trying the model again


class Embedder:
    def __init__(self, gateway, model: str, sim):
        self.gateway = gateway
        self.model = model
        self.sim = sim  # holds memory_vectors and pending_vectors
        self.failed_until = 0.0
        self.calls = 0
        self._cache: dict = {}

    def relevance(self, query: str, memories: list) -> np.ndarray | None:
        """0–1 per memory (min-max scaled cosine), or None to use shared words instead."""
        if time.monotonic() < self.failed_until or not memories:
            return None
        if not self._ensure(memories):
            return None
        q = self._embed([f"search_query: {query}"])
        if q is None:
            return None
        cosine = self._matrix(memories) @ q[0]
        low, high = float(cosine.min()), float(cosine.max())
        if high - low < 1e-6:
            return np.zeros(len(memories))
        return (cosine - low) / (high - low)

    def _matrix(self, memories) -> np.ndarray:
        """The memories' vectors as one matrix, cached until the set of memories changes
        (an agent's stream only grows at the end, and forgetting changes its length)."""
        key = (len(memories), memories[0].id, memories[-1].id, id(memories[0]))
        cached = self._cache.get(key)
        if cached is None:
            cached = np.stack([self.sim.memory_vectors[m.id] for m in memories])
            if len(self._cache) > 256:
                self._cache.clear()
            self._cache[key] = cached
        return cached

    def _ensure(self, memories) -> bool:
        vectors = self.sim.memory_vectors
        missing = [m for m in memories if m.id not in vectors]
        for start in range(0, len(missing), BATCH):
            batch = missing[start : start + BATCH]
            result = self._embed([f"search_document: {m.text}" for m in batch])
            if result is None:
                return False
            for m, v in zip(batch, result, strict=True):
                vectors[m.id] = v
                self.sim.pending_vectors.append(m.id)
        return True

    def _embed(self, texts: list[str]) -> np.ndarray | None:
        self.calls += 1
        raw = self.gateway.embed(self.model, texts)
        if raw is None:
            self.failed_until = time.monotonic() + RETRY_AFTER
            return None
        matrix = np.asarray(raw, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-9)
