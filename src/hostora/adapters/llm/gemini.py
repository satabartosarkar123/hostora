from __future__ import annotations

import httpx

from hostora.adapters.llm.base import LlmClientError, Route

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiClient:
    name: Route = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    async def complete_json(self, system: str, user: str, max_tokens: int) -> str:
        url = f"{GEMINI_BASE_URL}/{self._model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.3,
                "maxOutputTokens": max_tokens,
            },
        }
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LlmClientError(f"Gemini network error: {exc}") from exc

        if resp.status_code == 429:
            raise LlmClientError("Gemini rate limit exceeded", rate_limited=True)
        if resp.status_code >= 400:
            raise LlmClientError(f"Gemini HTTP {resp.status_code}")

        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            if not isinstance(text, str) or not text.strip():
                raise LlmClientError("Gemini returned empty content")
            return text
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmClientError("Gemini response malformed") from exc

    async def complete(self, prompt: str, system: str = "", temperature: float = 0.3) -> str:
        url = f"{GEMINI_BASE_URL}/{self._model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": 1000,
            },
        }
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LlmClientError(f"Gemini network error: {exc}") from exc

        if resp.status_code == 429:
            raise LlmClientError("Gemini rate limit exceeded", rate_limited=True)
        if resp.status_code >= 400:
            raise LlmClientError(f"Gemini HTTP {resp.status_code}")

        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            if not isinstance(text, str) or not text.strip():
                raise LlmClientError("Gemini returned empty content")
            return text
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmClientError("Gemini response malformed") from exc
