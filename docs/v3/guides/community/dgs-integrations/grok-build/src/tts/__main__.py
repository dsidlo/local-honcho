"""``python -m tts`` speaks one Grok hook event from stdin."""

from __future__ import annotations

from tts.speak import log, main


def entry() -> int:
    """Run the speak hook and return its exit code."""
    try:
        main()
    except Exception as exc:
        log(f"error {exc}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(entry())
