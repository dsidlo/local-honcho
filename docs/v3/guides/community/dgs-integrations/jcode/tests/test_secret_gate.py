import json
import subprocess
import os
import sys

GATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "jcode_secret_gate.sh")

cases = [
    # (tool, stdin_payload, expect_exit, label)
    ("read", {"file_path": "/home/dsidlo/.local/lib/honcho/.env"}, 2, "read .env blocked"),
    ("read", {"file_path": "/home/dsidlo/workspace/FlexiTTS/README.md"}, 0, "read normal allowed"),
    ("bash", {"command": "cat ~/.env"}, 2, "bash reads .env blocked"),
    ("bash", {"command": "cat >> README.md <<'EOF' ... mentions auth.json denylist ... EOF"}, 0, "doc mention allowed"),
    ("bash", {"command": "ls -la src"}, 0, "normal bash allowed"),
    ("grep", {"pattern": "key", "path": "/home/dsidlo/.jcode/auth.json"}, 2, "grep auth.json blocked"),
    ("bash", {"command": "PGPASSWORD=secret psql -c 'x'"}, 2, "PGPASSWORD assignment blocked"),
    ("bash", {"command": "git commit -m 'update docs'"}, 0, "git command allowed"),
]

fails = 0
for tool, payload, want, label in cases:
    proc = subprocess.run(
        ["bash", GATE],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "JCODE_HOOK_TOOL_NAME": tool},
    )
    status = "PASS" if proc.returncode == want else "FAIL"
    if proc.returncode != want:
        fails += 1
    detail = (proc.stderr or proc.stdout or "").strip()[:60]
    print(f"[{status}] {label}: exit {proc.returncode} (want {want}) {detail}")

print(f"\n{len(cases)-fails}/{len(cases)} passed")
sys.exit(1 if fails else 0)