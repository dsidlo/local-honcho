#!/usr/bin/env bash
# run-coverage.sh: run the jcode honcho integration suites with 100% coverage,
# including subprocess children (MCP server, tailer) via the coverage .pth hook.
#
# Usage (from the monorepo root):
#   bash docs/v3/guides/community/dgs-integrations/jcode/run-coverage.sh
#
# Works in two modes: with a live local Honcho every suite runs; without one
# the @live-marked e2e suites skip automatically. The 100% gate in both modes
# covers src/ (the integration code); full per-file reports print afterward.
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

# The 100% gate covers the integration SOURCES. Test-file coverage is shown
# informationally: with a live Honcho every file measures 100%; without one
# the @live-marked e2e suites skip and test-file lines drop (~95% total).
python3 -m coverage report --rcfile="$JDIR/coverage.config" -m   --include="$JDIR/src/*" --fail-under=100
python3 -m coverage report --rcfile="$JDIR/coverage.config" -m
echo "coverage: 100% source gate passed"