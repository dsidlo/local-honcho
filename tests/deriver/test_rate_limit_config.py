# pyright: reportPrivateUsage=false
"""Config knobs for the LLM rate-limit strategy (Phase 1)."""

import os
import subprocess
import sys

from src.config import LLMSettings


def test_defaults_match_plan_table() -> None:
    cfg = LLMSettings()
    assert cfg.RETRY_RATELIMIT_ENABLED is True
    assert cfg.RETRY_RATELIMIT_QUICK_RETRIES == 3
    assert cfg.RETRY_RATELIMIT_QUICK_DELAY_MS == 1500
    assert cfg.RETRY_RATELIMIT_COOLDOWN_SEC == 60
    assert cfg.RETRY_RATELIMIT_MAX_DEFERRALS == 5
    assert cfg.RETRY_RATELIMIT_ACTIVE_SINCE_METRIC is True


def test_kwargs_override_defaults() -> None:
    cfg = LLMSettings(
        RETRY_RATELIMIT_ENABLED=False,
        RETRY_RATELIMIT_QUICK_RETRIES=7,
        RETRY_RATELIMIT_QUICK_DELAY_MS=2000,
        RETRY_RATELIMIT_COOLDOWN_SEC=120,
        RETRY_RATELIMIT_MAX_DEFERRALS=9,
        RETRY_RATELIMIT_ACTIVE_SINCE_METRIC=False,
    )
    assert cfg.RETRY_RATELIMIT_ENABLED is False
    assert cfg.RETRY_RATELIMIT_QUICK_RETRIES == 7
    assert cfg.RETRY_RATELIMIT_QUICK_DELAY_MS == 2000
    assert cfg.RETRY_RATELIMIT_COOLDOWN_SEC == 120
    assert cfg.RETRY_RATELIMIT_MAX_DEFERRALS == 9
    assert cfg.RETRY_RATELIMIT_ACTIVE_SINCE_METRIC is False


def test_env_overrides_apply() -> None:
    """The LLM_RETRY_RATELIMIT_* env vars map to the knobs (Phase 1)."""
    env = os.environ.copy()
    env.update(
        {
            "LLM_RETRY_RATELIMIT_ENABLED": "false",
            "LLM_RETRY_RATELIMIT_QUICK_RETRIES": "7",
            "LLM_RETRY_RATELIMIT_QUICK_DELAY_MS": "2000",
            "LLM_RETRY_RATELIMIT_COOLDOWN_SEC": "120",
            "LLM_RETRY_RATELIMIT_MAX_DEFERRALS": "9",
            "LLM_RETRY_RATELIMIT_ACTIVE_SINCE_METRIC": "false",
        }
    )
    code = (
        "from src.config import LLMSettings; "
        "c=LLMSettings(); "
        "print(c.RETRY_RATELIMIT_ENABLED, c.RETRY_RATELIMIT_QUICK_RETRIES, "
        "c.RETRY_RATELIMIT_QUICK_DELAY_MS, c.RETRY_RATELIMIT_COOLDOWN_SEC, "
        "c.RETRY_RATELIMIT_MAX_DEFERRALS, c.RETRY_RATELIMIT_ACTIVE_SINCE_METRIC)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=os.getcwd(),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    parts = proc.stdout.strip().split()
    assert parts == ["False", "7", "2000", "120", "9", "False"]
