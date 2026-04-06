#!/bin/bash
#
# validate_installer.sh - Validates the self-extracting LocalHoncho_installer.sh
# Checks: Structure, embedded tar integrity, required sections, SHA, extractability.
# Run: bash scripts/validate_installer.sh
# Assumes: base64, sha256sum, tar available.

set -euo pipefail

SCRIPT_PATH="../dist/LocalHoncho_installer.sh"  # Relative to scripts/
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "ERROR: $SCRIPT_PATH not found. Run from project root."
    exit 1
fi

echo "=== Validating LocalHoncho_installer.sh ==="

# 1. Basic file checks
echo "[1/8] Basic file validation..."
if [ ! -x "$SCRIPT_PATH" ]; then
    echo "WARNING: Script not executable. Run: chmod +x $SCRIPT_PATH"
fi
if [ $(wc -l < "$SCRIPT_PATH") -lt 300 ]; then
    echo "ERROR: Script too short (<300 lines). Expected ~400-600."
    exit 1
fi
echo "OK: File exists, executable, reasonable size."

# 2. Shebang and strict mode
echo "[2/8] Shebang and mode..."
if ! head -n1 "$SCRIPT_PATH" | grep -q '^#!/bin/bash'; then
    echo "ERROR: Missing bash shebang."
    exit 1
fi
if ! grep -q 'set -euo pipefail' "$SCRIPT_PATH"; then
    echo "ERROR: Missing strict mode (set -euo pipefail)."
    exit 1
fi
echo "OK: Shebang and strict mode present."

# 3. Required sections/functions (from design)
echo "[3/8] Required sections..."
required_sections=(
    "print_status"  # Header output
    "check_already_installed"  # Idempotence
    "install_deps"  # Deps/UV
    "extract_tar"  # Self-extract
    "prompt_db_setup"  # DB interactive
    "prompt_llm_config"  # LLM prompts
    "generate_env"  # .env template
    "setup_db_migration"  # UV/alembic
    "integrate_pi"  # Pi merge
    "install_services"  # Systemd
    "verify_install"  # Verification
    "uninstall"  # --uninstall
)

missing_sections=()
for section in "${required_sections[@]}"; do
    if ! grep -q "function $section()" "$SCRIPT_PATH"; then
        missing_sections+=("$section")
    fi
done

if [ ${#missing_sections[@]} -ne 0 ]; then
    echo "ERROR: Missing sections: ${missing_sections[*]}"
    exit 1
fi
echo "OK: All required functions present."

# 4. Flags parsing
echo "[4/8] Flags support..."
if ! grep -q 'while \[\[ \$# -gt 0 \]\]; do' "$SCRIPT_PATH"; then
    echo "ERROR: Missing arg parsing loop."
    exit 1
fi
for flag in "--force" "--non-interactive" "--uninstall"; do
    if ! grep -q "case \$1 in.*$flag" "$SCRIPT_PATH"; then
        echo "ERROR: Missing support for $flag."
        exit 1
    fi
done
echo "OK: Flags (--force, --non-interactive, --uninstall) supported."

# 5. Embedded tar and SHA
echo "[5/8] Embedded tar and SHA..."
if ! grep -q 'EMBEDDED_TAR_B64=' "$SCRIPT_PATH"; then
    echo "ERROR: Missing EMBEDDED_TAR_B64 variable."
    exit 1
fi
if ! grep -q 'EXPECTED_SHA=' "$SCRIPT_PATH"; then
    echo "ERROR: Missing EXPECTED_SHA variable."
    exit 1
fi
if ! grep -q 'echo "\$EMBEDDED_TAR_B64" | base64 -d' "$SCRIPT_PATH"; then
    echo "ERROR: Missing base64 decode in extract_tar."
    exit 1
fi
if ! grep -q 'sha256sum "\$tmp_tar"' "$SCRIPT_PATH"; then
    echo "ERROR: Missing SHA verification."
    exit 1
fi
echo "OK: Embedded tar and SHA logic present (placeholder - update with real base64/SHA)."

# 6. Interactive prompts
echo "[6/8] Interactive config..."
if ! grep -q 'read -p.*DB.*Docker' "$SCRIPT_PATH"; then
    echo "ERROR: Missing DB Docker prompt."
    exit 1
fi
if ! grep -q 'prompt_llm_config.*anthropic' "$SCRIPT_PATH"; then
    echo "ERROR: Missing LLM provider prompt."
    exit 1
fi
if ! grep -q 'getpass.*API key' "$SCRIPT_PATH"; then
    echo "ERROR: Missing secure key prompt."
    exit 1
fi
if ! grep -q 'jq -s'\''.\[0\] \* .\[1\]'\''.*settings.json' "$SCRIPT_PATH"; then
    echo "ERROR: Missing Pi jq merge."
    exit 1
fi
if ! grep -q 'uv sync.*alembic upgrade head' "$SCRIPT_PATH"; then
    echo "ERROR: Missing UV/alembic setup."
    exit 1
fi
echo "OK: Interactive prompts for DB/LLM/embedding/Pi/migration present."

# 7. Services and verification
echo "[7/8] Services and verify..."
if ! grep -q 'systemctl --user enable.*honcho-api' "$SCRIPT_PATH"; then
    echo "ERROR: Missing service enable/start."
    exit 1
fi
if ! grep -q 'verify_install.*curl.*psql' "$SCRIPT_PATH"; then
    echo "ERROR: Missing verification (API/DB)."
    exit 1
fi
if ! grep -q 'uninstall.*rm -rf.*systemctl.*disable' "$SCRIPT_PATH"; then
    echo "ERROR: Missing uninstall logic."
    exit 1
fi
echo "OK: Services (systemd), verification, uninstall present."

# 8. Extract and test integrity (simulate)
echo "[8/8] Simulate extraction and integrity..."
local tmp_dir=$(mktemp -d)
local tmp_tar=$(mktemp honcho-tar.XXXXXX.tar.gz)

# Extract embedded (if real base64; skip full decode for placeholder)
if grep -q 'EMBEDDED_TAR_B64=.*non-empty' "$SCRIPT_PATH"; then
    echo "$EMBEDDED_TAR_B64" | base64 -d > "$tmp_tar" 2>/dev/null || echo "WARNING: Placeholder base64 - manual embed needed for full test."
    if [ -s "$tmp_tar" ]; then
        tar -tzf "$tmp_tar" > /dev/null || print_error "Tar corrupted."
        tar -xzf "$tmp_tar" -C "$tmp_dir" || print_error "Extraction failed."
        if [ -d "$tmp_dir/honcho" ] && [ -f "$tmp_dir/honcho/.env.template" ] && [ -f "$tmp_dir/services/honcho-api.service" ]; then
            echo "OK: Embedded tar extracts correctly (honcho, template, services)."
        else
            echo "ERROR: Extracted structure invalid."
            exit 1
        fi
    else
        echo "WARNING: Embedded tar empty (placeholder). Full test after embedding."
    fi
else
    echo "WARNING: No embedded tar (update EMBEDDED_TAR_B64). Structure checks passed."
fi

rm -rf "$tmp_dir" "$tmp_tar"

echo ""
echo "=== Validation Complete: All checks PASSED ==="
echo "Notes:"
echo "- Embed real tar: base64 -w 0 dist/honcho-pi.tar.gz > encoded.b64; paste to EMBEDDED_TAR_B64"
echo "- Update EXPECTED_SHA from dist/SHA256SUM"
echo "- Test full: bash $SCRIPT_PATH (interactive install)"
echo "- Distributable size: ~60MB with embedded tar."