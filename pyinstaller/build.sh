#!/bin/bash
# Build script for creating PyInstaller-based standalone executable
# Usage: ./pyinstaller/build.sh [--clean]

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Honcho Pi Build Script ==="
echo "Project root: $PROJECT_ROOT"
echo "Script dir: $SCRIPT_DIR"

# Parse arguments
CLEAN=0
while [[ $# -gt 0 ]]; do
    case $1 in
        --clean)
            CLEAN=1
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Clean if requested
if [[ $CLEAN -eq 1 ]]; then
    echo "Cleaning build directories..."
    rm -rf "$PROJECT_ROOT/pyinstaller/build"
    rm -rf "$PROJECT_ROOT/pyinstaller/dist"
fi

# Ensure PyInstaller is installed
if ! python3 -c "import PyInstaller" 2>/dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
fi

# Install package dependencies
echo "Installing package dependencies..."
pip install -e "$PROJECT_ROOT/pyinstaller" --quiet

# Run build
echo "Building executable..."
python3 "$PROJECT_ROOT/pyinstaller/build-scripts/pyinstaller-build.py"

echo ""
echo "=== Build Complete ==="
echo "Primary outputs (in pyinstaller/):"
echo "  - pyinstaller/dist/honcho-pi"
echo "  - pyinstaller/dist/honcho-pi-linux-x86_64.tar.gz"
echo ""
echo "Convenience copies (in project root dist/):"
echo "  - dist/honcho-pi"
echo "  - dist/honcho-pi-linux-x86_64.tar.gz"
echo ""
ls -lh "$PROJECT_ROOT/pyinstaller/dist/"