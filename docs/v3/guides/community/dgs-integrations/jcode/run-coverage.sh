#!/usr/bin/env bash
# run-coverage.sh: run the jcode honcho integration suites with 100% coverage,
# including subprocess children (MCP server, tailer) via the coverage .pth hook.
#
# Usage (from the monorepo root):
#   bash docs/v3/guides/community/dgs-integrations/jcode/run-coverage.sh
#
# Requires a running local Honcho for the @live-marked integration suites;
# everything else (unit + hook suites) runs offline.
set -euo pipefail
JDIR="$(cd "$(dirname "$0")" && pwd)"
DATADIR="$(mktemp -d "${TMPDIR:-/tmp}/jcode-honcho-cov.XXXXXX")"

cat > "$JDIR/coverage.config" <<EOF
[run]
branch = True
parallel = True
data_file = $DATADIR/.coverage

[report]
exclude_lines =
    pragma: no cover
    if __name__ == .__main__.:
EOF

cleanup() {
  rm -f "$JDIR/coverage.config"
  rm -rf "$DATADIR"
}
trap cleanup EXIT

COVERAGE_PROCESS_START="$JDIR/coverage.config" python3 -m pytest \
  "$JDIR/tests" -c "$JDIR/pytest.local.ini" "$@"

python3 -m coverage combine --rcfile="$JDIR/coverage.config" -q
python3 -m coverage report --rcfile="$JDIR/coverage.config" -m --fail-under=100
echo "coverage: 100% gate passed"