# PyInstaller Migration Summary

This document summarizes the changes made to migrate from PyApp to PyInstaller.

## What Changed

### Build System

| Aspect | Before (PyApp) | After (PyInstaller) |
|--------|----------------|----------------------|
| Build tool | PyApp (Rust-based) | PyInstaller (Python-based) |
| Build command | `cargo install pyapp` + env vars | `python pyinstaller-build.py` or `pyinstaller honcho-pi.spec` |
| Build time | Requires Rust toolchain | Requires Python + pip |
| Binary size | ~5 MB + downloads Python | ~15-30 MB (all bundled) |
| First run | Downloads Python | Ready immediately |

### Files Added/Modified

#### New Files

1. **`pyinstaller-build.py`** - Build automation script
2. **`honcho-pi.spec`** - PyInstaller specification file
3. **`.gitignore`** - Git ignore rules for PyInstaller artifacts

#### Modified Files

1. **`src/honcho_pi/services.py`**
   - Added `get_package_data_dir()` for PyInstaller compatibility
   - Updated `get_template_dir()` to use package_data directory

2. **`src/honcho_pi/pi_integration.py`**
   - Added `get_package_data_dir()` for PyInstaller compatibility
   - Refactored `install_extension()` to copy from packaged file
   - Reads actual `honcho.ts` instead of generating from template

3. **`pyproject.toml`**
   - Updated `[tool.hatch.build.targets.wheel]` to include package_data

4. **`docs/v3/guides/community/dgs-integrations/Local-Honcho-Installer-Design.md`**
   - Updated documentation for PyInstaller approach
   - Added comparison table
   - Updated architecture diagrams

5. **`README.md`**
   - Updated for PyInstaller-based distribution
   - Updated build instructions

### Package Data

Files copied into `src/honcho_pi/package_data/`:

```
package_data/
├── pi-extension/
│   ├── honcho.ts              (from docs/.../pi-mono/agent/extensions/)
│   ├── README-honcho.md       (from docs/.../pi-mono/agent/extensions/)
│   └── settings-snippet.json  (from pi-extension/templates/)
└── systemd/
    ├── honcho-api.service     (from docs/.../systemd/.config/systemd/user/)
    ├── honcho-deriver.service (from docs/.../systemd/.config/systemd/user/)
    └── README.md              (from docs/.../systemd/)
```

## How PyInstaller Package Data Works

### At Build Time

1. `pyinstaller-build.py` collects data files:
   ```python
   data_files = [
       ("src/honcho_pi/package_data/pi-extension", "honcho_pi/package_data/pi-extension"),
       ("src/honcho_pi/package_data/systemd", "honcho_pi/package_data/systemd"),
   ]
   ```

2. PyInstaller bundles these into the executable

### At Runtime

1. PyInstaller extracts data to a temporary directory
2. `sys._MEIPASS` points to this directory
3. Code checks for `_MEIPASS` attribute:
   ```python
   if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
       base_path = Path(sys._MEIPASS)  # PyInstaller bundle
   else:
       base_path = Path(__file__).parent  # Development
   ```

## Build Commands

### Quick Build
```bash
cd pyapp-distribution
pip install pyinstaller -e .
python pyinstaller-build.py
```

### Using Spec File Directly
```bash
cd pyapp-distribution
pip install pyinstaller -e .
pyinstaller honcho-pi.spec
```

### Build Options
```bash
# Clean build
python pyinstaller-build.py --clean

# Directory-based (faster startup, larger)
python pyinstaller-build.py --onedir
```

## Testing the Build

```bash
# Verify binary was created
ls -lh dist/honcho-pi

# Test the binary
./dist/honcho-pi --version
./dist/honcho-pi --help

# Test package data access
./dist/honcho-pi doctor
```

## Packaging for Distribution

```bash
# Create tarball
tar czf honcho-pi-linux-x86_64.tar.gz -C dist honcho-pi

# Upload to GitHub Releases
# (See .github/workflows/build.yml)
```

## Advantages of PyInstaller

1. **No Rust required** - Pure Python tooling
2. **Familiar technology** - Widely used in Python community
3. **True standalone** - No network required on first run
4. **Fast startup** - No Python download/bootstrap
5. **Mature ecosystem** - Extensive documentation and community

## Migration Notes

### For End Users
- No change required - same CLI interface
- Installation process unchanged
- Configuration files compatible

### For Developers
- Build process changed from `cargo` to `pyinstaller`
- Package data access via `sys._MEIPASS`
- No other code changes needed

## References

- [PyInstaller Documentation](https://pyinstaller.org/)
- [PyInstaller Runtime Information](https://pyinstaller.org/en/stable/runtime-information.html)
- [Original PyApp Documentation](https://ofek.dev/pyapp/)
