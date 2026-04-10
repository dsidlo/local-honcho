# Honcho Pi - PyInstaller Distribution

This directory contains the PyInstaller-based build system for creating standalone executables of the Honcho memory service for pi-mono.

## Quick Build

```bash
# From project root
./pyinstaller/build.sh [--clean]

# Output will be in dist/
#  - dist/honcho-pi                    (standalone executable)
#  - dist/honcho-pi-linux-x86_64.tar.gz  (distribution tarball)
```

## Directory Structure

```
pyinstaller/
├── build.sh                    # Main build script (run from project root)
├── build-scripts/
│   ├── pyinstaller-build.py   # Python build automation
│   └── honcho-pi.spec         # PyInstaller spec file
├── src/
│   └── honcho_pi/             # Python source code
│       ├── cli.py
│       ├── bootstrap.py
│       ├── config.py
│       ├── services.py
│       ├── pi_integration.py
│       ├── package_data/      # Files bundled in executable
│       │   ├── pi-extension/  # Pi extension (honcho.ts, etc.)
│       │   └── systemd/       # Service templates
│       └── commands/
├── pyproject.toml             # Package configuration
├── .env.template              # Configuration template
└── README.md                  # This file
```

## Requirements

- Python 3.10+
- PyInstaller (`pip install pyinstaller`)
- Linux x86_64 (for builds targeting Linux)

## Build Options

```bash
# Standard build
./pyinstaller/build.sh

# Clean build (removes build/ and dist/ first)
./pyinstaller/build.sh --clean

# Using PyInstaller directly
python pyinstaller/build-scripts/pyinstaller-build.py

# Using spec file
pyinstaller pyinstaller/build-scripts/honcho-pi.spec
```

## Output

- **Binary**: `dist/honcho-pi` (119 MB)
- **Tarball**: `dist/honcho-pi-linux-x86_64.tar.gz` (117 MB)

The binary is a standalone ELF executable that includes:
- Python 3.12 interpreter
- All Python dependencies (click, rich, pydantic, typer, etc.)
- Package data (extension files, service templates)

## Installation (End Users)

```bash
# Download and extract
curl -sL https://github.com/dsidlo/honcho/releases/latest/download/honcho-pi-linux-x86_64.tar.gz | tar xz
sudo mv honcho-pi /usr/local/bin/
sudo chmod +x /usr/local/bin/honcho-pi

# Run installer
honcho-pi install --non-interactive
```

## Development

### Installing in Development Mode

```bash
cd /path/to/honcho
pip install -e pyinstaller/
```

### Running Tests

```bash
cd pyinstaller
pytest tests/
```

## License

AGPL-3.0 (same as Honcho)
