import httpx
import respx
from pydantic import BaseModel

from hostora.adapters.llm.gemini import GEMINI_BASE_URL, GeminiClient
from hostora.adapters.llm.groq import GROQ_URL, GroqClient
from hostora.adapters.llm.mock import FailingClient, MockLlmClient
from hostora.adapters.llm.router import LlmRouter


class _SampleOut(BaseModel):
    picks: list[str]


async def test_llm_router_groq_429_falls_back_to_gemini() -> None:
    groq = GroqClient(api_key="test-groq", model="llama-3.1-8b-instant")
    gemini = GeminiClient(api_key="test-gemini", model="gemini-2.5-flash")
    router = LlmRouter([groq, gemini], cache=None, data_version="v1")

    with respx.mock(assert_all_called=True) as mock_http:
        mock_http.post(GROQ_URL).mock(return_value=httpx.Response(429, json={"error": "rate"}))
        mock_http.post(f"{GEMINI_BASE_URL}/gemini-2.5-flash:generateContent").mock(
            return_value=httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": '{"picks": ["veg_biryani"]}'}]}}]
                },
            )
        )

        res = await router.complete("sys", "user prompt", _SampleOut)
        assert res is not None
        out, route = res
        assert route == "gemini"
        assert out.picks == ["veg_biryani"]


async def test_llm_router_malformed_json_repairs_once() -> None:
    groq = GroqClient(api_key="test-groq", model="llama-3.1-8b-instant")
    router = LlmRouter([groq], cache=None, data_version="v1")

    with respx.mock(assert_all_called=True) as mock_http:
        route_mock = mock_http.post(GROQ_URL)
        route_mock.side_effect = [
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not valid json"}}]},
            ),
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"picks": ["jeera_rice"]}'}}]},
            ),
        ]

        res = await router.complete("sys", "user prompt", _SampleOut)
        assert res is not None
        out, route = res
        assert route == "groq"
        assert out.picks == ["jeera_rice"]
        assert route_mock.call_count == 2


async def test_llm_router_circuit_breaker_opens_after_3_failures() -> None:
    failing = FailingClient()
    router = LlmRouter([failing], cache=None, data_version="v1")

    for _ in range(5):
        res = await router.complete("sys", "clean user prompt", _SampleOut)
        assert res is None

    # Circuit breaker opens after 3 consecutive failures, so calls 4 and 5 skip the client
    assert failing.calls == 3


async def test_llm_router_privacy_guard_refuses_dirty_prompt() -> None:
    mock_client = MockLlmClient(candidates_response={"picks": ["veg_biryani"]})
    router = LlmRouter([mock_client], cache=None, data_version="v1")

    res = await router.complete(
        "sys",
        "Call me at +91 9876543210 or Sharma Family",
        _SampleOut,
        forbidden_terms=["Sharma Family"],
    )
    assert res is None
    assert len(mock_client.recorded_prompts) == 0


async def test_db_llm_cache_put_and_get() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from pydantic import SecretStr

    from hostora.adapters.llm.router import DbLlmCache, build_llm_router, get_llm_router
    from hostora.config import Settings

    mock_session = AsyncMock()
    mock_sessionmaker = MagicMock(return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock()
    mock_tx.__aexit__ = AsyncMock()
    mock_session.begin = MagicMock(return_value=mock_tx)

    mock_row = MagicMock()
    mock_row.route = "groq"
    mock_row.text = '{"picks": ["paneer"]}'
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_row
    mock_session.execute.return_value = mock_res

    cache = DbLlmCache(mock_sessionmaker)
    await cache.put("test_key", "groq", '{"picks": ["paneer"]}')
    got = await cache.get("test_key")
    assert got == ('{"picks": ["paneer"]}', "groq")

    # None return
    mock_res.scalar_one_or_none.return_value = None
    assert await cache.get("missing_key") is None

    s = Settings(
        env="dev",
        database_url="postgresql://localhost/test",
        secret_key=SecretStr("x" * 32),
        token_hmac_secret=SecretStr("x" * 32),
        phone_enc_key=SecretStr("c29tZS0zMi1ieXRlLXBob25lLWVuYy1rZXktYjY0MDE="),
        store_token_key=SecretStr("c29tZS0zMi1ieXRlLXN0b3JlLXRva2VuLWtleS1iNjQ="),
        pass_signing_key=SecretStr("0" * 64),
        pass_public_key="0" * 64,
        cron_secret=SecretStr("x" * 32),
        groq_api_key=SecretStr("gsk_test"),
        gemini_api_key=SecretStr("gem_test"),
        gemini_allowed=True,
    )
    import os

    orig_test = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        r = build_llm_router(s, sessionmaker=mock_sessionmaker, data_version="v2")
        assert len(r._clients) == 2
        r2 = get_llm_router()
        assert r2 is not None
    finally:
        if orig_test:
            os.environ["PYTEST_CURRENT_TEST"] = orig_test
