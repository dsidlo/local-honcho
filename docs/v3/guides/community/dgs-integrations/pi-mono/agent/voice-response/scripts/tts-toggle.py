#!/usr/bin/env python3
"""Run Grok TTS's toggle from Pi: engine, device, and per-engine volume.

The toggle lives in the grok-build package; this wrapper fixes the import
path so any python3 can run it. Arguments pass through unchanged:

  tts-toggle.py [on|off|toggle|status|engine|device|voices|piper-voice|volume] [...]

Examples:
  tts-toggle.py volume 7        # set volume for the CURRENT engine (1-10)
  tts-toggle.py engine piper    # switch speech engine
  tts-toggle.py device cpu      # restart the Kokoro daemon on CPU
  tts-toggle.py status          # one-line summary

State lives in the TTS config (GROK_TTS_CONFIG override, default
~/.grok/tts/config.json). Each engine's volume is stored separately
(kokoro.volume and piper.volume), so switching engines keeps levels.
"""

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

from tts.toggle import main  # noqa: E402


if __name__ == "__main__":
    main()