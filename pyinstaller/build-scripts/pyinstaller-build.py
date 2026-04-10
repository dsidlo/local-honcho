#!/usr/bin/env python3
"""Build script for creating PyInstaller-based standalone executable for honcho-pi.

This script is called from the project root directory (honcho/).

Usage:
    python pyinstaller/build-scripts/pyinstaller-build.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def get_project_root() -> Path:
    """Get project root directory (parent of pyinstaller/)."""
    # This script is at: pyinstaller/build-scripts/pyinstaller-build.py
    # Project root is two levels up
    return Path(__file__).parent.parent.parent.resolve()


def get_script_dir() -> Path:
    """Get the pyinstaller/ script directory."""
    return Path(__file__).parent.parent.resolve()


def check_pyinstaller() -> bool:
    """Check if PyInstaller is installed."""
    try:
        import PyInstaller
        return True
    except ImportError:
        return False


def install_pyinstaller():
    """Install PyInstaller if not present."""
    print("Installing PyInstaller...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def collect_data_files() -> list:
    """Collect data files to include in the build."""
    script_dir = get_script_dir()
    data_files = []
    
    # Package data files
    pkg_data_dir = script_dir / "src" / "honcho_pi" / "package_data"
    
    if pkg_data_dir.exists():
        for subdir in ["pi-extension", "systemd"]:
            src_dir = pkg_data_dir / subdir
            if src_dir.exists():
                # PyInstaller data format: (source, dest_in_bundle)
                data_files.append((str(src_dir), f"honcho_pi/package_data/{subdir}"))
    
    return data_files


def build_executable(onefile: bool = True, clean: bool = False):
    """Build the PyInstaller executable."""
    project_root = get_project_root()
    script_dir = get_script_dir()
    src_dir = script_dir / "src"
    
    # Output directories (inside pyinstaller/)
    pyinstaller_dir = project_root / "pyinstaller"
    build_dir = pyinstaller_dir / "build"
    dist_dir = pyinstaller_dir / "dist"
    
    # Create directories
    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    
    if clean and build_dir.exists():
        print(f"Removing {build_dir}")
        shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True, exist_ok=True)
    
    if clean and dist_dir.exists():
        print(f"Removing {dist_dir}")
        shutil.rmtree(dist_dir)
        dist_dir.mkdir(parents=True, exist_ok=True)
    
    # Always ensure directories exist
    pyinstaller_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    
    # Ensure PyInstaller is installed
    if not check_pyinstaller():
        install_pyinstaller()
    
    # Build the command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=honcho-pi",
        "--log-level=INFO",
        f"--workpath={build_dir}",
        f"--distpath={dist_dir}",
    ]
    
    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")
    
    # Add data files
    data_files = collect_data_files()
    for src, dest in data_files:
        cmd.append(f"--add-data={src}:{dest}")
    
    # Additional options
    cmd.extend([
        "--hidden-import=honcho_pi",
        "--hidden-import=honcho_pi.cli",
        "--hidden-import=honcho_pi.commands",
        "--hidden-import=honcho_pi.config",
        "--hidden-import=honcho_pi.services",
        "--hidden-import=honcho_pi.pi_integration",
        "--hidden-import=click",
        "--hidden-import=rich",
        "--hidden-import=pydantic",
        "--hidden-import=typer",
        "--hidden-import=jinja2",
        "--hidden-import=toml",
        "--hidden-import=requests",
        "--hidden-import=httpx",
        "--noupx",
        "--strip",
    ])
    
    # Entry point script
    entry_script = src_dir / "honcho_pi" / "__main__.py"
    cmd.append(str(entry_script))
    
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(project_root))
    
    # Output summary
    output_path = dist_dir / "honcho-pi"
    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"\n✓ Build successful!")
        print(f"  Output: {output_path}")
        print(f"  Size: {size_mb:.1f} MB")
        
        # Create tarball in pyinstaller directory
        tarball_path = dist_dir / "honcho-pi-linux-x86_64.tar.gz"
        subprocess.run(
            ["tar", "czf", str(tarball_path), "-C", str(dist_dir), "honcho-pi"],
            check=True
        )
        tb_size_mb = tarball_path.stat().st_size / (1024 * 1024)
        print(f"  Tarball: {tarball_path} ({tb_size_mb:.1f} MB)")
        
        # Also copy to project root for convenience
        root_dist = project_root / "dist"
        root_dist.mkdir(parents=True, exist_ok=True)
        root_output = root_dist / "honcho-pi"
        if root_output.exists():
            root_output.unlink()
        shutil.copy2(output_path, root_output)
        root_tarball = root_dist / "honcho-pi-linux-x86_64.tar.gz"
        if root_tarball.exists():
            root_tarball.unlink()
        shutil.copy2(tarball_path, root_tarball)
        print(f"  (also copied to project root: {root_output})")
    else:
        print("\n⚠ Output file not found - check build logs above")


if __name__ == "__main__":
    build_executable()
