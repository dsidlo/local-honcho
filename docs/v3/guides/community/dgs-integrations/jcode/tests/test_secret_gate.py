"""Secret-gate hook tests: drive src/jcode_secret_gate.sh as jcode would.

The gate is a bash pre_tool hook: exit 0 = allow, exit 2 = block with a
message on stderr (shown to the model), anything else fails open.

Run: pytest tests/test_secret_gate.py (pure bash, no Honcho needed)
"""

import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "src", "jcode_secret_gate.sh")

# (tool, stdin_payload, expected exit, label)
CASES = [
    ("read", {"file_path": "/home/dsidlo/.local/lib/honcho/.env"}, 2, "read .env blocked"),
    ("read", {"file_path": "/home/dsidlo/workspace/FlexiTTS/README.md"}, 0, "read normal allowed"),
    ("read", {"path": "/home/u/server.pem"}, 2, "read path-key .pem blocked"),
    ("read", {}, 0, "read no path allowed"),
    ("bash", {"command": "cat ~/.env"}, 2, "bash reads .env blocked"),
    ("bash", {"command": "head /home/dsidlo/.jcode/auth.json"}, 2, "bash auth.json blocked"),
    ("bash", {"command": "cat >> README.md <<'EOF' ... mentions auth.json denylist ... EOF"}, 0,
     "doc mention allowed"),
    ("bash", {"command": "ls -la src"}, 0, "normal bash allowed"),
    ("bash", {"command": "PGPASSWORD=secret psql -c 'x'"}, 2, "PGPASSWORD assignment blocked"),
    ("bash", {"command": "OPENAI_API_KEY=sk-abcdef psql"}, 2, "OPENAI_API_KEY assignment blocked"),
    ("bash", {"command": "GEMINI_API_KEY=AIzaSyabc123 export && env"}, 2, "GEMINI_API_KEY assignment blocked"),
    ("bash", {"command": "HF_TOKEN=hf_abcdef0123456789 huggingface-cli whoami"}, 2, "HF_TOKEN assignment blocked"),
    ("bash", {"command": "XAI_API_KEY=xai-vabcdef0123456789 curl API"}, 2, "XAI_API_KEY assignment blocked"),
    ("bash", {"command": "psql postgresql://dsidlo:rexrabbit@localhost:5433/honcho"}, 2,
     "direct credential reference blocked"),
    ("bash", {"command": "psql -c \"select * from x where y LIKE '%rexrabbit%'\""}, 2,
     "known credential LIKE-scan blocked"),
    ("bash", {"command": "git commit -m 'update docs'"}, 0, "git command allowed"),
    ("grep", {"pattern": "key", "path": "/home/dsidlo/.jcode/auth.json"}, 2, "grep auth.json blocked"),
    ("grep", {"pattern": "key", "path": "/home/dsidlo/workspace/honcho/src"}, 0, "grep normal allowed"),
    ("edit", {"file_path": "/home/x/.env"}, 0, "unknown tool fails open"),
]


def run_gate(tool: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", GATE],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "JCODE_HOOK_TOOL_NAME": tool},
    )


@pytest.mark.parametrize("tool,payload,want,label", CASES, ids=[c[3] for c in CASES])
def test_gate(tool, payload, want, label):
    proc = run_gate(tool, payload)
    assert proc.returncode == want, (
        f"{label}: exit {proc.returncode} (want {want}) "
        f"stderr={(proc.stderr or '').strip()[:80]}"
    )
    if want == 2:
        assert proc.stderr.strip(), f"{label}: blocked call must explain itself on stderr"


def test_gate_ignores_malformed_json():
    proc = subprocess.run(
        ["bash", GATE], input="not json", capture_output=True, text=True,
        env={**os.environ, "JCODE_HOOK_TOOL_NAME": "read"},
    )
    assert proc.returncode == 0  # fail-open on unparseable input


def test_gate_allows_empty_tool_env():
    proc = subprocess.run(
        ["bash", GATE], input=json.dumps({"command": "ls"}), capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "JCODE_HOOK_TOOL_NAME"},
    )
    assert proc.returncode == 0