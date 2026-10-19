#!/usr/bin/env python3
"""Print one engine's sample gain from the saved 1-10 volume level.

Usage: tts-gain.py [kokoro|piper]

Reads the TTS config (GROK_TTS_CONFIG override, default
~/.grok/tts/config.json) and maps the saved level to a multiplier with the
exact same rules the Kokoro daemon applies internally (tts.volume.level).
Prints e.g. ``0.70`` for level 7. Legacy stored floats such as ``0.8`` map
to the nearest level first.
"""

import json
import os
import sys
from pathlib import Path

_SRC = Path(
    os.environ.get(
        "GROK_TTS_SRC",
        "/home/dsidlo/workspace/honcho/docs/v3/guides/community/"
        "dgs-integrations/grok-build/src",
    )
)
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tts import paths  # noqa: E402
from tts.volume import (  # noqa: E402
    KOKORO_DEFAULT,
    PIPER_DEFAULT,
    engine_multiplier,
)


def current_engine() -> str:
    """Return the configured engine for "current" lookups."""
    try:
        data = json.loads(paths.config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "kokoro"
    engine = data.get("engine") if isinstance(data, dict) else None
    return engine if engine in ("kokoro", "piper") else "kokoro"


if __name__ == "__main__":
    engine = sys.argv[1].strip().lower() if len(sys.argv) > 1 else "current"
    if engine == "current":
        engine = current_engine()
    default = PIPER_DEFAULT if engine == "piper" else KOKORO_DEFAULT
    try:
        data = json.loads(paths.config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    print(f"{engine_multiplier(data, engine, default):.2f}")