#!/bin/bash
# embed_tar.sh - Embed real tar into LocalHoncho_installer.sh for self-containment
# Run: bash scripts/embed_tar.sh (from project root)
# Requires: base64, sha256sum, sed (built-in)

set -euo pipefail

TAR_PATH="../dist/honcho-pi.tar.gz"
INSTALLER_PATH="../dist/LocalHoncho_installer.sh"
if [ ! -f "$TAR_PATH" ]; then
    echo "ERROR: $TAR_PATH not found. Run python scripts/dist_script.py first."
    exit 1
fi

if [ ! -f "$INSTALLER_PATH" ]; then
    echo "ERROR: $INSTALLER_PATH not found. Create base installer first."
    exit 1
fi

echo "=== Embedding Tar into $INSTALLER_PATH ==="

# Compute SHA
SHA=$(sha256sum "$TAR_PATH" | cut -d' ' -f1)
echo "SHA: $SHA"

# Generate base64 (no wrap)
BASE64_CONTENT=$(base64 -w 0 "$TAR_PATH")
BASE64_LENGTH=${#BASE64_CONTENT}
echo "Base64 generated ($BASE64_LENGTH chars)"

# Backup installer
cp "$INSTALLER_PATH" "${INSTALLER_PATH}.bak.$(date +%s)"

# Replace EMBEDDED_TAR_B64 (remove placeholder, insert full)
sed -i "/EMBEDDED_TAR_B64=/,/^$/c\\
EMBEDDED_TAR_B64=\"$BASE64_CONTENT\"" "$INSTALLER_PATH"

# Replace EXPECTED_SHA
sed -i "s/EXPECTED_SHA=\\\".*\\\"/EXPECTED_SHA=\\\"$SHA\\\"/" "$INSTALLER_PATH"

echo "Embedded! Script size: $(wc -c < "$INSTALLER_PATH") bytes (~$(($(wc -c < "$INSTALLER_PATH") / 1024 / 1024)) MB)"

# Validate
if bash scripts/validate_installer.sh; then
    echo "Validation passed - self-contained!"
else
    echo "Validation failed - check errors."
    exit 1
fi

echo "Done. Test: bash $INSTALLER_PATH"