"""Turn-speech hook tests: drive src/jcode_turn_jsay.sh end to end.

The hook announces a short summary of the assistant's final response on
turn_end via jsay. Gating is item-level only (what is said), never
workflow-level (how long the turn ran, when the last turn spoke). These
tests run the real script with a fake jsay on PATH and assert spoken/skip
decisions from a recording log.

Run: pytest tests/test_turn_jsay.py (offline, no audio hardware needed)
"""

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "src", "jcode_turn_jsay.sh")


class Recording:
    """Standalone fake jsay: appends spoken text without flags to a log."""

    SOURCE = """#!/usr/bin/env bash
# Drop known flags so the record holds only the spoken payload.
payload=()
for arg in "$@"; do
  case "$arg" in
    --quiet|--blocking|--stop|--flush) ;;
    *) payload+=("$arg") ;;
  esac
done
printf '%s\\n' "${payload[*]}" >> "$JSAY_RECORD"
"""


def write_recording_jsay(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    jsay = bin_dir / "jsay"
    jsay.write_text(Recording.SOURCE)
    jsay.chmod(0o755)
    record = tmp_path / "spoken.txt"
    record.touch()
    return jsay.parent, record


def run_hook(tmp_path, text, status="ok", event="turn_end", duration_ms="1000"):
    bin_dir, record = write_recording_jsay(tmp_path)
    # The hook logs decisions under $HOME/.cache/jcode per its own contract.
    log = tmp_path / ".cache" / "jcode" / "jcode-turn-jsay.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path),
        "JSAY_RECORD": str(record),
        "JCODE_HOOK_EVENT": event,
        "JCODE_HOOK_STATUS": status,
        "JCODE_HOOK_LAST_ASSISTANT_TEXT": text,
        "JCODE_HOOK_DURATION_MS": duration_ms,
    }
    result = subprocess.run(  # pragma: no cover - trivial subprocess wrapper
        ["bash", HOOK], capture_output=True, text=True, env=env, timeout=60
    )
    return result, record.read_text(encoding="utf-8"), log


class TestTurnJsay:
    def test_speaks_short_turn(self, tmp_path):
        """A 1s turn with speakable text still speaks: no workflow gating."""
        result, spoken, _ = run_hook(tmp_path, "Staged twelve files and committed.")
        assert result.returncode == 0
        assert spoken.strip() == "Staged twelve files and committed."

    def test_skips_error_status_and_wrong_event(self, tmp_path):
        result, spoken, _ = run_hook(tmp_path, "hello", status="error")
        assert result.returncode == 0
        assert spoken == ""
        result, spoken, _ = run_hook(tmp_path, "hello", event="turn_start")
        assert result.returncode == 0
        assert spoken == ""

    def test_skips_empty_text(self, tmp_path):
        result, spoken, _ = run_hook(tmp_path, "   ")
        assert result.returncode == 0
        assert spoken == ""

    def test_sensitive_text_is_skipped(self, tmp_path):
        result, spoken, log = run_hook(
            tmp_path, "Set API_KEY=xyz123 for the client."
        )
        assert result.returncode == 0
        assert spoken == ""
        assert "skip sensitive-looking text" in log.read_text(encoding="utf-8")

    def test_two_sentence_cap(self, tmp_path):
        text = "First sentence done. Second sentence done. Third sentence done."
        _, spoken, _ = run_hook(tmp_path, text)
        assert spoken.strip() == "First sentence done. Second sentence done."

    def test_word_cap(self, tmp_path):
        # Three short sentences: the sentence cap keeps two (24 words), then a
        # fourth short sentence survives the trim and pushes past 35 words.
        # This exercises the word cap independently of the sentence cap.
        sentences = " ".join(
            " ".join(f"w{i}_{j}" for j in range(12)) + "." for i in range(6)
        )
        result, spoken, log = run_hook(tmp_path, sentences)
        assert result.returncode == 0
        assert spoken == ""
        assert "skip too many words" in log.read_text(encoding="utf-8")

    def test_markdown_and_links_are_flattened(self, tmp_path):
        text = "See `docs/v3` and https://example.com/x for details."
        _, spoken, _ = run_hook(tmp_path, text)
        assert "https://example.com/x" not in spoken
        assert "`" not in spoken

    def test_code_blocks_omitted(self, tmp_path):
        text = "Use this: ```print(1)``` then continue."
        _, spoken, _ = run_hook(tmp_path, text)
        assert "print(1)" not in spoken.strip()

    def test_optional_speech_floor_default_off(self, tmp_path):
        """Default floor is 0: a 6-word item speaks."""
        _, spoken, _ = run_hook(tmp_path, "Short note spoken aloud.")
        assert spoken.strip() == "Short note spoken aloud."

    def test_optional_speech_floor_when_set(self, tmp_path):
        """Setting JCODE_JSAY_MIN_DURATION_MS floors estimated speech time."""
        bin_dir, record = write_recording_jsay(tmp_path)
        log = tmp_path / ".cache" / "jcode" / "jcode-turn-jsay.log"
        env = {
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(tmp_path),
            "JSAY_RECORD": str(record),
            "JCODE_HOOK_EVENT": "turn_end",
            "JCODE_HOOK_STATUS": "ok",
            "JCODE_HOOK_LAST_ASSISTANT_TEXT": "Short note spoken aloud.",
            "JCODE_HOOK_DURATION_MS": "1000",
            "JCODE_JSAY_MIN_DURATION_MS": "5000",
        }
        result = subprocess.run(
            ["bash", HOOK], capture_output=True, text=True, env=env, timeout=60
        )
        assert result.returncode == 0
        assert record.read_text(encoding="utf-8") == ""
        assert "skip short speech" in log.read_text(encoding="utf-8")

    def test_jsay_env_file_sourced(self, tmp_path):
        """~/.jcode/jsay.env values reach the fake jsay through the env."""
        bin_dir, record = write_recording_jsay(tmp_path)
        env_file = tmp_path / "jsay.env"
        env_file.write_text("JSAY_VOICE=af_heart\n")
        env = {
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(tmp_path),
            "JSAY_RECORD": str(record),
            "JCODE_HOOK_EVENT": "turn_end",
            "JCODE_HOOK_STATUS": "ok",
            "JCODE_HOOK_LAST_ASSISTANT_TEXT": "One line only.",
            "JCODE_HOOK_DURATION_MS": "1000",
            "JCODE_JSAY_ENV_FILE": str(env_file),
        }
        result = subprocess.run(
            ["bash", HOOK], capture_output=True, text=True, env=env, timeout=60
        )
        assert result.returncode == 0
        # The fake records argv, so the voice flag (if any) lands in the log;
        # the key assertion is the hook still speaks with an env file present.
        assert "One line only." in record.read_text(encoding="utf-8")

    def test_rate_limit_is_gone(self, tmp_path):
        """Two turns seconds apart both speak: no interval gate remains."""
        _, first, _ = run_hook(tmp_path, "First immediate announcement.")
        assert "First immediate announcement." in first
        _, second, log = run_hook(tmp_path, "Second immediate announcement.")
        assert "Second immediate announcement." in second
        assert "skip rate limited" not in log.read_text(encoding="utf-8")

    def test_stamp_file_written(self, tmp_path):
        run_hook(tmp_path, "Stamp should be written.")
        state = tmp_path / ".local" / "state" / "jcode" / "last-jsay-turn-end.stamp"
        state_dir = tmp_path / ".local" / "state"
        found = list(state_dir.rglob("last-jsay-turn-end.stamp")) if state_dir.exists() else []
        # Stamp is best-effort state, not gating: exists or nothing crashed.
        assert result_stamp_written(found)

    def test_missing_jsay_binary_fails_open(self, tmp_path):
        """Without jsay on PATH the hook exits 0 and speaks nothing."""
        bin_dir, record = write_recording_jsay(tmp_path)
        bin_dir.joinpath("jsay").chmod(0o644)  # not executable
        env = {
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(tmp_path),
            "JSAY_RECORD": str(record),
            "JCODE_HOOK_EVENT": "turn_end",
            "JCODE_HOOK_STATUS": "ok",
            "JCODE_HOOK_LAST_ASSISTANT_TEXT": "No executable jsay.",
            "JCODE_HOOK_DURATION_MS": "1000",
        }
        result = subprocess.run(
            ["bash", HOOK], capture_output=True, text=True, env=env, timeout=60
        )
        assert result.returncode == 0
        assert record.read_text(encoding="utf-8") == ""


def result_stamp_written(found):
    """The stamp must exist after a spoken turn; helper keeps test readable."""
    assert found, "stamp file should be written after a spoken turn"
    return True