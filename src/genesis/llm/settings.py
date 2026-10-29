"""The `[llm]` table of a world config. See docs/05-llm-and-memory.md (5.5)."""

from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool = False  # off: rules decide everything (fast, no Ollama needed)
    provider: str = "ollama"  # or "openai": vLLM, llama.cpp llama-server, SGLang, LM Studio
    url: str = "http://127.0.0.1:11434"  # Ollama; GENESIS_OLLAMA_URL overrides it
    chat_model: str = "llama3"  # greetings, conversation turns, asking for help
    mind_model: str = "llama3"  # hard choices: give or refuse, tell the truth or lie, give in
    timeout_seconds: float = 20.0
    max_calls_per_tick: int = 4  # 0 = no cap (use the governor instead, M8f)
    max_calls_per_day: int = 150  # 0 = no cap
    temperature: float = 0.8
    max_output_tokens: int = 150
    conversation_turns: int = 3  # spoken turns in one free-time conversation (A, B, A)
    max_line_chars: int = 160  # longer lines are cut at a word boundary
    # Memory (M5)
    embed_model: str = "nomic-embed-text"  # memory search; without it, shared words are used
    memories_in_prompt: int = 6  # memories that "come to mind" in each prompt
    reflection: bool = True  # each agent reflects on its day, once a night
    reflection_max_tokens: int = 300
    # LLM governor (M8a): the most calls per real minute this machine handles (measure it with
    # `genesis bench-llm`). Chatter may use up to 90% of it, hard choices up to 100%, never
    # more; over it, the rules decide. 0 = no limit.
    max_calls_per_minute: int = 0
    # Where the LLM's time goes (M8f). Greetings and small talk can be left to the rules, so
    # the LLM is free for real decisions: each person's plan, inventing, goals, trading.
    voice_greetings: bool = True  # False: greetings are the rules' (no LLM call)
    chatter_share: float = 1.0  # share of free-time conversations the LLM voices (0..1)
    plan_every_days: float = 0  # each adult's own plan for the coming days (0 = no plans)
    # M8.5: model workers asked at once for independent questions (plans, reflections). Ollama
    # must allow it: OLLAMA_NUM_PARALLEL >= workers. Results are the same as one by one.
    workers: int = 1

    @classmethod
    def from_dict(cls, data: dict) -> "LLMConfig":
        types = {f.name: f.type for f in fields(cls)}
        unknown = set(data) - set(types)
        if unknown:
            raise ValueError(f"Unknown llm setting(s): {', '.join(sorted(unknown))}")

        for key, value in data.items():
            expected = types[key]
            if expected in (bool, "bool"):
                ok = isinstance(value, bool)
            elif expected in (str, "str"):
                ok = isinstance(value, str) and value != ""
            elif expected in (int, "int"):
                ok = isinstance(value, int) and not isinstance(value, bool)
            else:
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            if not ok:
                raise ValueError(f"llm.{key} must be a {getattr(expected, '__name__', expected)}")

        config = cls(**data)
        if config.max_calls_per_tick < 0 or config.max_calls_per_day < 0:
            raise ValueError("llm call budgets cannot be negative")
        if not 0 <= config.chatter_share <= 1:
            raise ValueError("llm.chatter_share must be 0..1")
        if config.provider not in ("ollama", "openai"):
            raise ValueError('llm.provider must be "ollama" or "openai"')
        if not 1 <= config.workers <= 16:
            raise ValueError("llm.workers must be 1-16")
        if config.plan_every_days < 0:
            raise ValueError("llm.plan_every_days cannot be negative")
        if not 1 <= config.conversation_turns <= 8:
            raise ValueError("llm.conversation_turns must be 1-8")
        if config.timeout_seconds <= 0:
            raise ValueError("llm.timeout_seconds must be positive")
        return config

    def to_dict(self) -> dict:
        return asdict(self)
