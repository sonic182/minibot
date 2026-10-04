from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

QuestionType = Literal["choice", "noul", "score"]


class DecisionQuestion(BaseModel):
    """One typed question for a decision model.

    ``choice`` takes ``{option: description}``, ``score`` an ordered list of level descriptions and
    ``noul`` (a yes/no probability) takes no criteria.
    """

    model_config = ConfigDict(extra="forbid")

    type: QuestionType
    instructions: str
    criteria: dict[str, str] | list[str] | None = None


class DecisionAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: QuestionType
    choice: str | None = None
    noul: float | None = None
    score: float | None = None
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = None


class DecisionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    answers: dict[str, DecisionAnswer]
    cost: float | None = None
    latency_seconds: float = 0.0


class DecisionHTTPError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class DecisionClient(Protocol):
    async def ask(self, state: dict[str, Any], questions: dict[str, DecisionQuestion]) -> DecisionResult: ...
