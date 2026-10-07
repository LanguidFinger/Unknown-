from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from ..policy.prompt import Prompt

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Usage:
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    parsed: T
    usage: Usage
    model_id: str
    raw_text: str


class LLMClient(Protocol):
    model_id: str

    def structured[M: BaseModel](self, prompt: Prompt, schema: type[M], *, max_output_tokens: int) -> LLMResult[M]: ...


def estimate_tokens(text: str) -> int:
    """Cheap pre-flight estimate (~4 chars/token). Real usage is charged after the call."""
    return max(1, len(text) // 4)
