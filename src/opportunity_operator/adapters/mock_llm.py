"""Scripted/recording LLM for tests. Needs no credentials and makes no network calls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..policy.prompt import Prompt
from ..ports.llm import LLMResult, Usage, estimate_tokens


@dataclass
class RecordedCall:
    rendered: str
    purpose: str
    destination: str
    fact_ids: list[str]
    schema: str


@dataclass
class MockLLM:
    responder: Callable[[Prompt, type[BaseModel]], BaseModel | dict[str, Any]]
    model_id: str = "mock-llm"
    calls: list[RecordedCall] = field(default_factory=list)

    def structured[M: BaseModel](self, prompt: Prompt, schema: type[M], *, max_output_tokens: int) -> LLMResult[M]:
        rendered = prompt.render()
        self.calls.append(RecordedCall(rendered, prompt.purpose.value, prompt.destination.value, prompt.fact_ids, schema.__name__))
        out = self.responder(prompt, schema)
        parsed = out if isinstance(out, schema) else schema.model_validate(out)
        raw = parsed.model_dump_json()
        return LLMResult(parsed, Usage(estimate_tokens(rendered), estimate_tokens(raw)), self.model_id, raw)
