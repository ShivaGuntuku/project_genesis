"""The only code that talks to Ollama.

One call = one chat request with a JSON schema (Ollama's `format`), so the model can only
answer in the expected shape. Failures never raise: they come back as a Reply with an
outcome ("offline", "timeout", "error"), and the caller falls back to the rules.

When Ollama can't be reached, the gateway stops trying for a while (`retry_after`), so a
stopped server costs almost nothing per decision.
"""

import time
from dataclasses import dataclass

import httpx

OK = "ok"
OFFLINE = "offline"
TIMEOUT = "timeout"
ERROR = "error"


@dataclass
class Reply:
    outcome: str  # ok | offline | timeout | error
    text: str = ""  # the model's raw answer (JSON text) when ok
    error: str = ""
    latency_ms: int = 0


class OllamaGateway:
    def __init__(
        self,
        url: str = "http://127.0.0.1:11434",
        timeout: float = 20.0,
        retry_after: float = 30.0,
        transport: httpx.BaseTransport | None = None,  # tests pass a mock transport
    ):
        self.url = url.rstrip("/")
        self.client = httpx.Client(base_url=self.url, timeout=timeout, transport=transport)
        self.retry_after = retry_after
        self.offline_until = 0.0
        self.last_error = ""

    # --------------------------------

    def available(self) -> bool:
        """False for a while after Ollama couldn't be reached."""
        return time.monotonic() >= self.offline_until

    def check(self) -> tuple[bool, list[str] | str]:
        """(True, installed model names) or (False, reason)."""
        try:
            response = self.client.get("/api/tags", timeout=3)
            response.raise_for_status()
        except httpx.HTTPError as e:
            return False, f"Ollama is not reachable at {self.url} ({type(e).__name__})"
        return True, [m["name"] for m in response.json().get("models", [])]

    def chat(
        self,
        model: str,
        system: str,
        prompt: str,
        schema: dict,
        temperature: float = 0.8,
        seed: int | None = None,
        max_tokens: int = 150,
    ) -> Reply:
        if not self.available():
            return Reply(OFFLINE, error=self.last_error or "offline")

        options = {"temperature": temperature, "num_predict": max_tokens}
        if seed is not None:
            options["seed"] = seed
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "format": schema,
            "stream": False,
            "options": options,
            "keep_alive": "30m",
        }

        started = time.monotonic()
        for attempt in range(2):  # one retry for a transient server error
            try:
                response = self.client.post("/api/chat", json=body)
            except httpx.ConnectError as e:
                return self._offline(f"Ollama is not reachable at {self.url}: {e}", started)
            except httpx.TimeoutException:
                return Reply(TIMEOUT, error="no answer in time", latency_ms=_ms(started))
            except httpx.HTTPError as e:
                if attempt == 0:
                    continue
                return Reply(ERROR, error=f"{type(e).__name__}: {e}", latency_ms=_ms(started))

            if response.status_code == 404:
                return Reply(
                    ERROR,
                    error=f"model {model!r} not found (run: ollama pull {model})",
                    latency_ms=_ms(started),
                )
            if response.status_code >= 500 and attempt == 0:
                continue
            if response.status_code != 200:
                return Reply(
                    ERROR,
                    error=f"HTTP {response.status_code}: {response.text[:200]}",
                    latency_ms=_ms(started),
                )
            try:
                text = response.json()["message"]["content"]
            except (ValueError, KeyError, TypeError):
                return Reply(ERROR, error="unexpected reply from Ollama", latency_ms=_ms(started))
            return Reply(OK, text=text, latency_ms=_ms(started))

        return Reply(ERROR, error="Ollama kept failing", latency_ms=_ms(started))

    def embed(self, model: str, texts: list[str]) -> list[list[float]] | None:
        """Embedding vectors for texts (Ollama /api/embed), or None on any failure."""
        if not self.available() or not texts:
            return None
        try:
            response = self.client.post(
                "/api/embed", json={"model": model, "input": texts, "keep_alive": "30m"}
            )
        except httpx.ConnectError as e:
            self._offline(f"Ollama is not reachable at {self.url}: {e}", time.monotonic())
            return None
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            vectors = response.json()["embeddings"]
        except (ValueError, KeyError, TypeError):
            return None
        return vectors if len(vectors) == len(texts) else None

    def close(self):
        self.client.close()

    # --------------------------------

    def _offline(self, message: str, started: float) -> Reply:
        self.offline_until = time.monotonic() + self.retry_after
        self.last_error = message
        return Reply(OFFLINE, error=message, latency_ms=_ms(started))


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


class OpenAIGateway(OllamaGateway):
    """Any OpenAI-compatible server (review: faster stacks than Ollama): vLLM, llama.cpp's
    llama-server, SGLang, LM Studio. Same contract as OllamaGateway: one chat request with a
    JSON schema (`response_format: json_schema`, guided decoding), failures never raise.

        [llm]
        provider = "openai"
        url = "http://127.0.0.1:8000"        # vLLM (llama-server: 8080, LM Studio: 1234)
        chat_model = mind_model = "meta-llama/Meta-Llama-3-8B-Instruct"
    """

    def __init__(
        self,
        url="http://127.0.0.1:8000",
        timeout=20.0,
        retry_after=30.0,
        transport=None,
        api_key: str = "",
    ):
        super().__init__(url, timeout, retry_after, transport)
        if api_key:
            self.client.headers["Authorization"] = f"Bearer {api_key}"

    def check(self) -> tuple[bool, list[str] | str]:
        try:
            response = self.client.get("/v1/models", timeout=3)
            response.raise_for_status()
        except httpx.HTTPError as e:
            return False, f"no OpenAI-compatible server at {self.url} ({type(e).__name__})"
        return True, [m["id"] for m in response.json().get("data", [])]

    def chat(self, model, system, prompt, schema, temperature=0.8, seed=None, max_tokens=150):
        if not self.available():
            return Reply(OFFLINE, error=self.last_error or "offline")
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema, "strict": True},
            },
        }
        if seed is not None:
            body["seed"] = seed
        started = time.monotonic()
        for attempt in range(2):
            try:
                response = self.client.post("/v1/chat/completions", json=body)
            except httpx.ConnectError as e:
                return self._offline(f"no server at {self.url}: {e}", started)
            except httpx.TimeoutException:
                return Reply(TIMEOUT, error="no answer in time", latency_ms=_ms(started))
            except httpx.HTTPError as e:
                if attempt == 0:
                    continue
                return Reply(ERROR, error=f"{type(e).__name__}: {e}", latency_ms=_ms(started))
            if response.status_code >= 500 and attempt == 0:
                continue
            if response.status_code != 200:
                return Reply(
                    ERROR,
                    error=f"HTTP {response.status_code}: {response.text[:200]}",
                    latency_ms=_ms(started),
                )
            try:
                text = response.json()["choices"][0]["message"]["content"]
            except (ValueError, KeyError, TypeError, IndexError):
                return Reply(
                    ERROR, error="unexpected reply from the server", latency_ms=_ms(started)
                )
            return Reply(OK, text=text, latency_ms=_ms(started))
        return Reply(ERROR, error="the server kept failing", latency_ms=_ms(started))

    def embed(self, model, texts):
        if not self.available() or not texts:
            return None
        try:
            response = self.client.post("/v1/embeddings", json={"model": model, "input": texts})
        except httpx.ConnectError as e:
            self._offline(f"no server at {self.url}: {e}", time.monotonic())
            return None
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            return [d["embedding"] for d in response.json()["data"]]
        except (ValueError, KeyError, TypeError):
            return None


def make_gateway(config, url: str | None = None):
    """The gateway the world's [llm] settings ask for (Ollama by default)."""
    import os

    url = url or config.url
    if getattr(config, "provider", "ollama") == "openai":
        return OpenAIGateway(
            url, timeout=config.timeout_seconds, api_key=os.environ.get("GENESIS_LLM_API_KEY", "")
        )
    return OllamaGateway(url, timeout=config.timeout_seconds)
