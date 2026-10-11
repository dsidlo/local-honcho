"""Promote one Honcho conclusion into a Grok memory topic.

The topic records the conclusion id and the tag ``from-honcho``. The same id
is not written twice. This never writes ``~/.grok/rules/SOUL.md``. It writes
only when ``[memory] enabled`` or ``[memory_v2] enabled`` is true.
``memory_v2`` wins when both are set, matching Grok's config order.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


class PromotionError(Exception):
    """The conclusion cannot be written as a topic."""


def memory_enabled(config_text: str) -> bool:
    """Return whether Grok memory is enabled in a ``config.toml`` document."""
    try:
        data = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return False
    v2 = data.get("memory_v2")
    if isinstance(v2, dict) and "enabled" in v2:
        return bool(v2["enabled"])
    memory = data.get("memory")
    if isinstance(memory, dict):
        return bool(memory.get("enabled", False))
    return False


def render_topic(conclusion: dict[str, Any]) -> str:
    """Render one conclusion as a memory topic."""
    conclusion_id = str(conclusion.get("id") or "").strip()
    content = str(conclusion.get("content") or "").strip()
    if not conclusion_id or not content:
        raise PromotionError("conclusion needs an id and content")
    observer = str(conclusion.get("observer_id") or conclusion.get("observer") or "")
    observed = str(conclusion.get("observed_id") or conclusion.get("observed") or "")
    workspace = str(conclusion.get("workspace") or "")
    return (
        "---\n"
        "tags: [from-honcho]\n"
        f"honcho_conclusion_id: {conclusion_id}\n"
        "---\n"
        "\n"
        f"# Honcho conclusion {conclusion_id}\n"
        "\n"
        f"Workspace `{workspace}`. Observer `{observer}`, observed `{observed}`.\n"
        "\n"
        f"{content}\n"
    )


def topic_path(topics_dir: Path, conclusion_id: str) -> Path:
    """Path of the topic file for one conclusion id."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in conclusion_id)
    return topics_dir / f"honcho-{safe}.md"


def promote(
    config_text: str, topics_dir: Path, conclusion: dict[str, Any]
) -> Path | None:
    """Write the topic, or return ``None`` when memory is off.

    An existing topic that already names the conclusion id is left unchanged.
    """
    if not memory_enabled(config_text):
        return None
    conclusion_id = str(conclusion.get("id") or "").strip()
    path = topic_path(topics_dir, conclusion_id)
    if path.is_file() and conclusion_id in path.read_text(encoding="utf-8"):
        return path
    topics_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(render_topic(conclusion), encoding="utf-8")
    return path
