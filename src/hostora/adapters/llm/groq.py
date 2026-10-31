from __future__ import annotations

import httpx

from hostora.adapters.llm.base import LlmClientError, Route

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqClient:
    name: Route = "groq"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    async def complete_json(self, system: str, user: str, max_tokens: int) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(GROQ_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LlmClientError(f"Groq network error: {exc}") from exc

        if resp.status_code == 429:
            raise LlmClientError("Groq rate limit exceeded", rate_limited=True)
        if resp.status_code >= 400:
            raise LlmClientError(f"Groq HTTP {resp.status_code}: {resp.text}")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LlmClientError("Groq returned empty content")
            return content
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmClientError("Groq response malformed") from exc

    async def complete(self, prompt: str, system: str = "", temperature: float = 0.3) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": 1000,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(GROQ_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LlmClientError(f"Groq network error: {exc}") from exc

        if resp.status_code == 429:
            raise LlmClientError("Groq rate limit exceeded", rate_limited=True)
        if resp.status_code >= 400:
            raise LlmClientError(f"Groq HTTP {resp.status_code}: {resp.text}")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LlmClientError("Groq returned empty content")
            return content
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmClientError("Groq response malformed") from exc
