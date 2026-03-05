from __future__ import annotations

from typing import Any

from minibot.llm.services import LLMExecutionProfile


class ResponseMetadataService:
    def __init__(self, llm_client: Any) -> None:
        self._profile = LLMExecutionProfile.from_client(llm_client)

    def provider_name(self) -> str | None:
        return self._profile.provider_name

    def model_name(self) -> str | None:
        return self._profile.model_name

    def response_metadata(self, should_reply: bool) -> dict[str, Any]:
        return {
            "should_reply": should_reply,
            "llm_provider": self.provider_name(),
            "llm_model": self.model_name(),
        }
