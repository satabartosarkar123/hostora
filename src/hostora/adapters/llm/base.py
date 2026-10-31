from __future__ import annotations

from typing import Literal, Protocol

Route = Literal["groq", "gemini", "template", "manual"]


class LlmClientError(Exception):
    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


class LlmClient(Protocol):
    name: Route

    async def complete_json(self, system: str, user: str, max_tokens: int) -> str: ...
    async def complete(self, prompt: str, system: str = "", temperature: float = 0.0) -> str: ...
