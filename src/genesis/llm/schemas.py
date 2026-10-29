"""What the model must answer, and checking the answer.

Every answer has the same small shape (short outputs are fast; see doc 13):

    {"thought": "why, privately", "choice": "<one option key>", "say": "what I say out loud"}

`choice` is left out when there is nothing to choose (a greeting, asking for help).
The JSON schema restricts `choice` to the allowed keys; `parse` checks again, because the
simulation never trusts the model.
"""

import json
import re

from pydantic import BaseModel, ValidationError, field_validator


class InvalidAnswer(ValueError):
    pass


class Answer(BaseModel):
    thought: str = ""
    choice: str | None = None
    say: str

    @field_validator("thought", "say", mode="before")
    @classmethod
    def _text(cls, value):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("must be text")
        return value


def schema(keys: list[str] | None) -> dict:
    """JSON schema for Ollama's `format`. Property order is the order the model writes in:
    first the private thought, then the choice, then the words."""
    properties: dict = {"thought": {"type": "string"}}
    if keys:
        properties["choice"] = {"type": "string", "enum": list(keys)}
    properties["say"] = {"type": "string"}
    return {"type": "object", "properties": properties, "required": list(properties)}


def parse(text: str, keys: list[str] | None, max_chars: int = 160) -> Answer:
    """The model's raw answer → a checked Answer. Raises InvalidAnswer."""
    try:
        answer = Answer.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        raise InvalidAnswer(f"not a valid answer: {_short(e)}") from e

    if keys:
        if answer.choice not in keys:
            raise InvalidAnswer(f"choice {answer.choice!r} is not one of {keys}")
    else:
        answer.choice = None

    answer.say = clean_line(answer.say, max_chars)
    answer.thought = clean_line(answer.thought, max_chars)
    if not answer.say:
        raise InvalidAnswer("said nothing")
    return answer


def clean_line(text: str, max_chars: int) -> str:
    """One tidy spoken line: no surrounding quotes, no 'Name:' prefix, no line breaks."""
    text = re.sub(r"<!--|-->|[<>{}\[\]*_#`]", " ", text)  # stray markup from the model
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[A-Z][a-z]+:\s+", "", text)  # "Mira: hello" → "hello"
    text = text.strip().strip("\"'“”").strip()
    if len(text) > max_chars:
        cut = text[: max_chars - 1]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        text = cut.rstrip(",;:") + "…"
    return text


def _short(error: Exception) -> str:
    return str(error).splitlines()[0][:120]
