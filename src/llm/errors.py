"""Translate provider SDK transport failures into Honcho's own error type.

Every backend call funnels through `src.llm.request_builder`, so this is the one
place that has to know what a provider outage looks like across the three SDKs.
They do not share a base class, but they split failures the same two ways:

* the request never got a response -- each SDK has its own connection-error
  class (timeouts subclass it), and google-genai surfaces raw httpx errors, or
  a bare `TimeoutError` on its aiohttp transport;
* the request got a response carrying a server-side status -- anthropic and
  openai expose it as `status_code`, google-genai as `code`.
"""

from __future__ import annotations

import anthropic
import httpx
import openai

from src.exceptions import UpstreamLLMError

# Anything at or above this is the provider (or a proxy in front of it) failing,
# not a malformed request from us. 4xx stays untranslated so genuine client
# errors -- and 429, which callers throttle on rather than retry blindly -- keep
# surfacing as themselves.
_SERVER_ERROR_FLOOR = 500

# "Never reached the provider." Neither anthropic's nor openai's class derives
# from httpx's, so each must be named; httpx and `TimeoutError` cover
# google-genai, which surfaces transport failures unwrapped.
_CONNECTION_ERRORS = (
    anthropic.APIConnectionError,
    openai.APIConnectionError,
    httpx.TransportError,
    TimeoutError,
)


def _status_of(exc: BaseException) -> int | None:
    """Best-effort HTTP status from a provider SDK exception.

    Set on the instance rather than the class, so this reads attributes instead
    of matching types: anthropic and openai use `status_code`, google `code`.
    """
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    return None


def as_upstream_error(exc: BaseException) -> UpstreamLLMError | None:
    """Return the 503 to raise for `exc`, or None to let it propagate as-is.

    Covers both halves of an outage: a request that never landed and one that
    landed on something returning 5xx -- a litellm proxy mid-restart produces
    each in turn.
    """
    if isinstance(exc, _CONNECTION_ERRORS):
        return UpstreamLLMError(f"Could not reach the model provider: {exc}")

    status = _status_of(exc)
    if status is not None and status >= _SERVER_ERROR_FLOOR:
        return UpstreamLLMError(f"Model provider returned HTTP {status}")

    return None


def raise_upstream_error(exc: BaseException) -> None:
    """Re-raise `exc` as an `UpstreamLLMError` when it looks like an outage.

    A no-op for anything else, so the caller's bare `raise` still applies.
    """
    upstream = as_upstream_error(exc)
    if upstream is not None:
        raise upstream from exc


def is_rate_limit_error(exc: BaseException) -> bool:
    """True when `exc` is a provider throttle (HTTP 429).

    Reuses the `_status_of` attribute-probing approach, so each SDK is
    recognized through what it exposes: anthropic/openai carry `status_code`,
    google-genai `code`. SDK-native `RateLimitError` types (anthropic, openai)
    and the `rate_limit_exceeded` surface string are also matched as a
    fallback for throttles that do not carry a reliable numeric status.
    """
    if _status_of(exc) == 429:
        return True
    for module in (anthropic, openai):
        rate_limit_cls = getattr(module, "RateLimitError", None)
        if rate_limit_cls is not None and isinstance(exc, rate_limit_cls):
            return True
    return "rate_limit_exceeded" in str(exc)


__all__ = ["as_upstream_error", "raise_upstream_error", "is_rate_limit_error"]
