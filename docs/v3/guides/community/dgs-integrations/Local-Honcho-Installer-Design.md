# Local Honcho Installer Design

## Overview

This document outlines the design for the **Honcho Pi Distribution** using [PyInstaller](https://pyinstaller.org/) — a mature Python-to-standalone-executable packager. The distribution provides a single native executable (`honcho-pi`) that includes the Python interpreter and all dependencies.

**Key Design Decision**: Uses PyInstaller to create true standalone binaries that require no Python installation on the target system.

**Goals**:
- Native binaries: `curl -sL https://github.com/dsidlo/honcho/releases/latest/download/honcho-pi-linux-x86_64.tar.gz | tar xz && sudo mv honcho-pi /usr/local/bin/`
- Self-managing: Built-in `self` commands for updates, status, and reconfiguration
- Idempotent: Safe to re-run; `honcho-pi install` detects existing installations
- Portable: Linux x86_64/ARM64 primary targets; standalone executable
- Versioned: GitHub releases with semantic versioning

**Target Users**: Pi-mono developers wanting local agentic memory without Python/virtualenv management. Install time: <2min on clean Ubuntu.

**Non-Goals**: Windows/Mac primary support (focus Linux x86_64/ARM64); managed/cloud Honcho (local-only).

---

## Architecture

### PyInstaller Native Binaries

PyInstaller generates platform-specific standalone executables:

| Platform | Output | Distribution |
|----------|--------|--------------|
| Linux x86_64 | `honcho-pi` ELF binary | `honcho-pi-linux-x86_64.tar.gz` |
| Linux ARM64 | `honcho-pi` ELF binary | `honcho-pi-linux-aarch64.tar.gz` |

Each binary:
- Contains embedded Python interpreter
- Includes all dependencies (click, rich, httpx, etc.)
- Includes package data (pi extension templates, systemd services)
- Can run on systems without Python installed

### Comparison: PyInstaller vs PyApp

| Feature | PyInstaller | PyApp |
|---------|-------------|-------|
| End-user Python required | ❌ No | ❌ No |
| Build tool required | pip install pyinstaller | Rust + cargo install pyapp |
| Binary size | ~15-30 MB | ~5 MB (downloads Python on first run) |
| First-run network | No | Yes (downloads Python) |
| Self-update | Via `self update` command | Built-in PyApp self-update |
| Startup speed | Fast | Slower (bootstrap first run) |
| Maintenance status | ✅ Actively maintained | ✅ Actively maintained |

### Project Structure

```
pyapp-distribution/
├── pyproject.toml              # PEP 518 package configuration
├── honcho-pi.spec              # PyInstaller specification file
├── pyinstaller-build.py        # Build automation script
├── src/honcho_pi/              # Main Python package
│   ├── __init__.py
│   ├── __main__.py             # Entry point for PyInstaller
│   ├── cli.py                  # Typer-based CLI with subcommands
│   ├── bootstrap.py            # Installation orchestration
│   ├── services.py             # Systemd service generation
│   └── package_data/           # Bundled templates and configs
│       ├── pi-extension/       # Pi extension files
│       │   ├── honcho.ts       # Main extension (copied from docs)
│       │   ├── README-honcho.md
│       │   └── settings-snippet.json
│       └── systemd/            # Service templates
│           ├── honcho-api.service
│           └── honcho-deriver.service
├── README.md                   # Package documentation
└── .github/workflows/
    └── release.yml             # CI/CD for GitHub Releases
```

---

## Build Process

### Building Locally

```bash
# Install PyInstaller
pip install pyinstaller

# Build single executable
python pyinstaller-build.py

# Or use spec file directly
pyinstaller honcho-pi.spec

# Output: dist/honcho-pi
```

### Build Options

```bash
# Single-file executable (default)
python pyinstaller-build.py --onefile

# Directory-based (faster startup, larger distribution)
python pyinstaller-build.py --onedir

# Clean build
python pyinstaller-build.py --clean
```

### CI/CD Pipeline

GitHub Actions workflow:

1. **Test**: Run pytest on Python package
2. **Build**: PyInstaller for each target platform
3. **Package**: Create tar.gz archives
4. **Release**: Upload to GitHub Releases

```yaml
strategy:
  matrix:
    include:
      - os: ubuntu-22.04
        target: x86_64-unknown-linux-gnu
        arch: x86_64
      - os: ubuntu-22.04
        target: aarch64-unknown-linux-gnu
        arch: aarch64
```

---

## Installation Flow

### First-Time Install (via curl)

```bash
# Download and extract
curl -sL https://github.com/dsidlo/honcho/releases/latest/download/honcho-pi-linux-x86_64.tar.gz | tar xz

# Move to PATH
sudo mv honcho-pi /usr/local/bin/
sudo chmod +x /usr/local/bin/honcho-pi

# Run installer (idempotent)
honcho-pi install --non-interactive
```

### Installation Steps

1. **OS Check**: Linux x86_64 or ARM64 only
2. **Dependency Check**: Verify systemd, docker available
3. **Directory Setup**: Create `~/.local/lib/honcho/`, `~/.config/honcho-pi/`
4. **Database**: Start PostgreSQL + pgvector via Docker
5. **Config**: Copy `.env.template` → `~/.config/honcho-pi/.env`
6. **Services**: Generate and enable systemd user services
7. **Pi Extension**: Copy `honcho.ts` to `~/.pi/agent/extensions/`

### Management Commands

```bash
# Update to latest release
honcho-pi self update

# Check installation status
honcho-pi self status

# Reconfigure
honcho-pi self configure

# Reset to clean state
honcho-pi self reset

# Run diagnostics
honcho-pi doctor
```

---

## Package Data Access

PyInstaller bundles package data into the executable. At runtime:

```python
import sys
from pathlib import Path
import pkgutil

def get_package_data_path(subpath: str) -> Path:
    """Get path to bundled package data (works in PyInstaller bundle)."""
    if getattr(sys, 'frozen', False):
        # Running in PyInstaller bundle
        base_path = Path(sys._MEIPASS)
    else:
        # Running in normal Python environment
        base_path = Path(__file__).parent
    
    return base_path / "package_data" / subpath

# Usage
extension_src = get_package_data_path("pi-extension/honcho.ts")
service_template = get_package_data_path("systemd/honcho-api.service")
```

---

## Configuration

### Build-time Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `HONCHO_PI_VERSION` | Version string for build | `1.0.0` |
| `HONCHO_PI_BUILD_DATE` | ISO8601 build timestamp | Auto-generated |

### Runtime Configuration

Stored in `~/.config/honcho-pi/`:

- `.env`: Environment variables (API keys, DB URLs)
- `services/`: Generated systemd service files
- `docker-compose-db.yml`: Database container config
- `config.toml`: Application configuration

---

## Distribution Strategy

### Release Channels

```bash
# Stable (default)
curl -sL .../releases/latest/download/honcho-pi-linux-x86_64.tar.gz

# Beta
# Download from beta release tag

# Development
# Build from source
```

### Verification

Binaries should include checksums for verification:

```bash
# SHA256 checksums
honcho-pi-linux-x86_64.tar.gz.sha256
honcho-pi-linux-aarch64.tar.gz.sha256
```

---

## Testing Strategy

### Build Validation

```bash
# Test built binary works
./dist/honcho-pi --version
./dist/honcho-pi --help

# Test install in clean container
docker run --rm -v $(pwd)/dist:/dist ubuntu:22.04 \
  bash -c "cp /dist/honcho-pi /usr/local/bin/ && honcho-pi doctor"
```

### Integration Tests

- Clean Ubuntu install
- Existing Honcho detection
- Pi extension installation
- Service management

---

## Migration from PyApp

If users have existing PyApp-based installations:

```bash
# 1. Backup existing config
cp ~/.config/honcho-pi/.env ~/.env.backup

# 2. Remove old binary
rm /usr/local/bin/honcho-pi  # Old PyApp version

# 3. Install new PyInstaller version
# (curl download steps)

# 4. Restore config if needed
# Settings are preserved in ~/.config/honcho-pi/
```

---

## Appendix: Files Changed from PyApp Design

| Aspect | PyApp | PyInstaller |
|--------|-------|-------------|
| Build script | `build.sh` | `pyinstaller-build.py` |
| Build config | Env vars (`PYAPP_*`) | `honcho-pi.spec` |
| Data files | Embedded via PyApp | `--add-data` or `Analysis.datas` |
| Template access | Env-based | Runtime path detection |
| Entry point | PyApp-managed | Direct `__main__.py` |
| First-run behavior | Downloads Python | Ready immediately |
| Update mechanism | `self update` re-downloads | `self update` re-downloads |

---

## References

- [PyInstaller Documentation](https://pyinstaller.org/en/stable/)
- [PyInstaller Spec Files](https://pyinstaller.org/en/stable/spec-files.html)
- [Building Standalone Apps](https://pyinstaller.org/en/stable/usage.html)
- [Honcho Pi Distribution README](../../implementation/local-honcho/README.md)
