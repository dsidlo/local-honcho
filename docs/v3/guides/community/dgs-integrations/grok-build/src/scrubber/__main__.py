"""``python -m scrubber`` reads one Grok hook event from stdin."""

from scrubber.hook import main


def entry() -> int:
    """Run the hook and return its exit code."""
    return main()


if __name__ == "__main__":
    raise SystemExit(entry())
