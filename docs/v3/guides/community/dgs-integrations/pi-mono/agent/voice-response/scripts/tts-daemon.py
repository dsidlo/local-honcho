#!/usr/bin/env python3
"""Kokoro TTS daemon. Implementation lives in the grok-build package."""

import sys
from pathlib import Path

_SRC = Path(
    "/home/dsidlo/workspace/honcho/docs/v3/guides/community/"
    "dgs-integrations/grok-build/src"
)
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tts.daemon import main  # noqa: E402

if __name__ == "__main__":
    main()
