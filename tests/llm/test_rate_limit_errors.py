# pyright: reportArgumentType=false
"""Rate-limit (429) classification and client quick-retry strategy.

Covers the `is_rate_limit_error` classifier (three SDK shapes), the
per-failure-class tenacity wait, the quick-budget propagation behavior of
`honcho_llm_call`, and the fallback-swap suppression on a 429.
"""

from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import openai
import pytest
from google.genai import errors as genai_errors

from src.config import ModelConfig, ResolvedFallbackConfig, settings
from src.exceptions import UpstreamLLMError
from src.llm import api as api_module
from src.llm.errors import is_rate_limit_error
from src.llm.runtime import (
    rate_limit_last_failure,
    select_model_config_for_attempt,
)

_REQUEST = httpx.Request("POST", "http://tentacruel-litellm.internal/v1/messages")


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=_REQUEST, text="rate limited")


def _anthropic_429() -> anthropic.APIStatusError:
    return anthropic.APIStatusError(
        "Error code: 429", response=_response(429), body=None
    )


def _openai_429() -> openai.RateLimitError:
    return openai.RateLimitError(
        "Rate limit reached: input token limit exceeded",
        response=_response(429),
        body=None,
    )


def _google_429() -> genai_errors.ClientError:
    return genai_errors.ClientError(429, {})


class FakeOutcome:
    def __init__(self, exc: BaseException | None) -> None:
        self._exc = exc

    def exception(self) -> BaseException | None:
        return self._exc


class FakeRetryState:
    def __init__(self, exc: BaseException | None, attempt_number: int = 2) -> None:
        self.outcome = FakeOutcome(exc)
        self.attempt_number = attempt_number


# --- Classifier (Phase 2) ---


@pytest.mark.parametrize(
    "exc",
    [
        _anthropic_429(),
        _openai_429(),
        _google_429(),
        # Fallback string match when no numeric status is present.
        Exception("some provider returned rate_limit_exceeded"),
    ],
)
def test_is_rate_limit_error_matches_throttles(exc: BaseException) -> None:
    assert is_rate_limit_error(exc)


@pytest.mark.parametrize(
    "exc",
    [
        anthropic.APIStatusError("4xx", response=_response(400), body=None),
        anthropic.APIStatusError("5xx", response=_response(502), body=None),
        openai.APIStatusError("5xx", response=_response(503), body=None),
        genai_errors.ServerError(502, {}),
        UpstreamLLMError("Model provider returned HTTP 503"),
        httpx.ConnectError("connection refused"),
        ValueError("not a provider error"),
    ],
)
def test_is_rate_limit_error_rejects_non_throttles(exc: BaseException) -> None:
    assert not is_rate_limit_error(exc)


# --- Client quick-retry wait (Phase 3) ---


def test_retry_wait_returns_quick_delay_for_429() -> None:
    wait = api_module._retry_wait(FakeRetryState(_openai_429()))
    assert wait == settings.LLM.RETRY_RATELIMIT_QUICK_DELAY_MS / 1000.0


def test_retry_wait_keeps_exponential_for_5xx() -> None:
    wait = api_module._retry_wait(
        FakeRetryState(UpstreamLLMError("Model provider returned HTTP 502"))
    )
    # Exponential floor is 4s, well above the 1.5s quick delay.
    assert wait >= 4.0


def test_retry_wait_uses_exponential_when_strategy_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.LLM, "RETRY_RATELIMIT_ENABLED", False)
    wait = api_module._retry_wait(FakeRetryState(_openai_429()))
    assert wait >= 4.0


# --- Quick budget propagation through honcho_llm_call (Phase 3) ---


async def test_429_surfaces_after_quick_budget() -> None:
    """A persistent 429 propagates as the provider error once the quick budget
    is spent (reraise=True), instead of becoming a RetryError or falling back."""
    calls = 0

    async def _always_429(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise _openai_429()

    with (
        patch.object(api_module, "honcho_llm_call_inner", new=_always_429),
        # Avoid real sleeps; the quick-delay selection is unit-tested above.
        patch.object(api_module, "_retry_wait", return_value=0.0),
        pytest.raises(openai.RateLimitError),
    ):
        await api_module.honcho_llm_call(
            model_config=ModelConfig(
                model="mercury-3",
                transport="openai",
                # Enough for the registry to build a client; it is never used,
                # since honcho_llm_call_inner is patched out.
                api_key="test-key",
            ),
            prompt="hi",
            max_tokens=64,
            enable_retry=True,
            retry_attempts=3,
        )

    assert calls == 3


async def test_timeout_still_retries_and_surfaces() -> None:
    """Non-429 failures keep the existing path: retried, then surfaced."""
    with (
        patch.object(
            api_module,
            "honcho_llm_call_inner",
            new=AsyncMock(
                side_effect=UpstreamLLMError("Model provider returned HTTP 500")
            ),
        ),
        patch.object(api_module, "_retry_wait", return_value=0.0),
        pytest.raises(UpstreamLLMError),
    ):
        await api_module.honcho_llm_call(
            model_config=ModelConfig(
                model="claude-haiku-4-5",
                transport="anthropic",
                api_key="test-key",
            ),
            prompt="hi",
            max_tokens=64,
            enable_retry=True,
            retry_attempts=2,
        )


# --- Fallback swap suppression (Phase 3 / runtime) ---


def _primary_with_fallback() -> ModelConfig:
    return ModelConfig(
        model="mercury-3",
        transport="openai",
        api_key="primary-key",
        fallback=ResolvedFallbackConfig(model="gpt-4o-mini", transport="openai"),
    )


def test_no_fallback_swap_on_rate_limit_last_failure() -> None:
    rate_limit_last_failure.set(True)
    try:
        selected = select_model_config_for_attempt(
            _primary_with_fallback(), attempt=3, retry_attempts=3
        )
    finally:
        rate_limit_last_failure.set(False)
    assert selected.model == "mercury-3"
    assert selected.transport == "openai"


def test_fallback_swap_still_happens_without_rate_limit() -> None:
    rate_limit_last_failure.set(False)
    selected = select_model_config_for_attempt(
        _primary_with_fallback(), attempt=3, retry_attempts=3
    )
    assert selected.model == "gpt-4o-mini"
    assert selected.transport == "openai"
