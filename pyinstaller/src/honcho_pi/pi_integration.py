"""Pi extension integration for honcho-pi.

Manages the Pi extension (.ts file) and settings integration.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import click


# Pi directories
PI_AGENT_DIR = Path.home() / ".pi/agent"
PI_EXTENSIONS_DIR = PI_AGENT_DIR / "extensions"
PI_SETTINGS_FILE = PI_AGENT_DIR / "settings.json"


# Legacy fallback template (used if packaged file not found)
HONCHO_EXTENSION_TEMPLATE = '''import { Extension } from '@pi/core';

// Honcho Pi Extension
const HONCHO_CONFIG = {
  apiUrl: '{{ honcho_api_url | default("http://localhost:8000") }}',
  workspace: '{{ honcho_workspace | default("default") }}',
  user: '{{ honcho_user | default("pi-user") }}',
  agentId: '{{ honcho_agent_id | default("agent-pi-mono") }}',
  enabled: {{ honcho_enabled | default(true) | lower }},
  observationalHooks: {{ enable_obs_hooks | default(true) | lower }},
  gitBranchTracking: {{ enable_git_tracking | default(true) | lower }},
  localCacheFallback: {{ enable_local_fallback | default(true) | lower }},
};

export default class HonchoExtension extends Extension {
  name = 'honcho-pi';
  version = '1.0.0';
  
  async onLoad() {
    if (!HONCHO_CONFIG.enabled) {
      console.log('[Honcho] Extension disabled');
      return;
    }
    console.log('[Honcho] Extension loaded');
  }
}
'''


def get_package_data_dir() -> Path:
    """Get the package data directory.
    
    Works both in development and when bundled with PyInstaller.
    In PyInstaller, sys._MEIPASS points to the temp extraction directory.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # PyInstaller sets _MEIPASS to extracted bundle directory
        base_path = Path(sys._MEIPASS)
        return base_path / "honcho_pi" / "package_data"
    else:
        # Development: use package-relative path
        package_dir = Path(__file__).parent
        return package_path / "package_data"


HONCHO_SETTINGS_SNIPPET = {
    "honcho": {
        "enabled": True,
        "apiUrl": "http://localhost:8000",
        "workspace": "default",
        "user": "pi-user",
        "agentId": "agent-pi-mono",
        "version": "1.0.0",
    }
}


def check_pi_installed() -> bool:
    """Check if Pi is installed."""
    return PI_AGENT_DIR.exists()


def is_installed() -> bool:
    """Check if Honcho extension is installed and enabled."""
    status = get_extension_status()
    return status["extension_present"] and status["enabled_in_settings"]


def ensure_pi_directories():
    """Ensure Pi extension directories exist."""
    if not check_pi_installed():
        raise RuntimeError(f"Pi not found at {PI_AGENT_DIR}. Install Pi first.")
    
    PI_EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return PI_EXTENSIONS_DIR


def install_extension(config: Optional[Dict[str, Any]] = None) -> bool:
    """Install honcho.ts extension to Pi.
    
    Copies the actual honcho.ts extension file from package data.
    The extension uses environment variables for configuration.
    
    Args:
        config: Configuration dict (legacy, not used but kept for API compatibility)
    """
    if not check_pi_installed():
        click.echo(click.style("✗ Pi not installed", fg="red"))
        click.echo(f"Install Pi first: {PI_AGENT_DIR}")
        return False
    
    try:
        ensure_pi_directories()
        
        # Copy extension file from package data
        ext_path = PI_EXTENSIONS_DIR / "honcho.ts"
        pkg_data_dir = get_package_data_dir()
        source_path = pkg_data_dir / "pi-extension" / "honcho.ts"
        
        if not source_path.exists():
            click.echo(click.style(f"✗ Extension source not found: {source_path}", fg="red"))
            click.echo("  This may indicate a packaging issue.")
            return False
        
        shutil.copy2(source_path, ext_path)
        click.echo(click.style(f"✓ Extension installed: {ext_path}", fg="green"))
        
        # Merge settings
        merge_settings()
        
        return True
        
    except Exception as e:
        click.echo(click.style(f"✗ Installation failed: {e}", fg="red"))
        return False


def merge_settings():
    """Merge Honcho settings into Pi's settings.json."""
    try:
        backup_file = PI_SETTINGS_FILE.with_suffix('.json.bak')
        
        # Load existing settings or create new
        if PI_SETTINGS_FILE.exists():
            shutil.copy2(PI_SETTINGS_FILE, backup_file)
            
            with open(PI_SETTINGS_FILE) as f:
                settings = json.load(f)
        else:
            settings = {}
            PI_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        # Merge Honcho settings
        if "honcho" not in settings:
            settings["honcho"] = HONCHO_SETTINGS_SNIPPET["honcho"].copy()
        else:
            # Update existing but preserve user changes
            settings["honcho"].update({
                k: v for k, v in HONCHO_SETTINGS_SNIPPET["honcho"].items()
                if k not in settings["honcho"]
            })
            settings["honcho"]["enabled"] = True
        
        # Write back
        with open(PI_SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        
        click.echo(click.style(f"✓ Settings merged: {PI_SETTINGS_FILE}", fg="green"))
        return True
        
    except json.JSONDecodeError as e:
        click.echo(click.style(f"✗ Invalid JSON in settings: {e}", fg="red"))
        return False
    except Exception as e:
        click.echo(click.style(f"✗ Settings merge failed: {e}", fg="red"))
        return False


def remove_extension() -> bool:
    """Remove Honcho extension from Pi."""
    errors = []
    
    # Remove extension file
    ext_path = PI_EXTENSIONS_DIR / "honcho.ts"
    if ext_path.exists():
        try:
            ext_path.unlink()
        except Exception as e:
            errors.append(f"Failed to remove {ext_path}: {e}")
    
    # Update settings to disable
    if PI_SETTINGS_FILE.exists():
        try:
            with open(PI_SETTINGS_FILE) as f:
                settings = json.load(f)
            
            if "honcho" in settings:
                settings["honcho"]["enabled"] = False
                
                with open(PI_SETTINGS_FILE, "w") as f:
                    json.dump(settings, f, indent=2)
        except Exception as e:
            errors.append(f"Failed to update settings: {e}")
    
    if errors:
        for err in errors:
            click.echo(click.style(f"✗ {err}", fg="yellow"))
        return False
    
    click.echo(click.style("✓ Extension removed", fg="green"))
    return True


def get_extension_status() -> Dict[str, Any]:
    """Get status of Pi extension installation."""
    status = {
        "pi_installed": check_pi_installed(),
        "extension_present": False,
        "enabled_in_settings": False,
        "api_url": None,
    }
    
    if not status["pi_installed"]:
        return status
    
    # Check extension file
    ext_path = PI_EXTENSIONS_DIR / "honcho.ts"
    status["extension_present"] = ext_path.exists()
    
    # Check settings
    if PI_SETTINGS_FILE.exists():
        try:
            with open(PI_SETTINGS_FILE) as f:
                settings = json.load(f)
            
            honcho_cfg = settings.get("honcho", {})
            status["enabled_in_settings"] = honcho_cfg.get("enabled", False)
            status["api_url"] = honcho_cfg.get("apiUrl")
        except Exception:
            pass
    
    return status


def prompt_for_install() -> bool:
    """Interactive prompt for Pi extension installation."""
    if not check_pi_installed():
        click.echo("Pi is not installed at ~/.pi/agent/")
        return False
    
    click.echo("Pi is installed. Configure Honcho integration?")
    
    if not click.confirm("Install Pi extension?", default=True):
        return False
    
    config = {
        "enable_hooks": click.confirm("Enable observation hooks?", default=True),
        "enable_git": click.confirm("Enable Git branch integration?", default=True),
    }
    
    return install_extension(config)


# Compatibility wrapper for bootstrap.py
class PiExtensionManager:
    """Pi extension manager for compatibility with bootstrap.py."""
    
    def is_pi_installed(self) -> bool:
        """Check if Pi is installed."""
        return check_pi_installed()
    
    def is_installed(self) -> bool:
        """Check if extension is installed."""
        return is_installed()
    
    def install(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Install Pi extension."""
        return install_extension(config)
    
    def uninstall(self) -> bool:
        """Remove Pi extension."""
        return remove_extension()
    
    def get_status(self) -> Dict[str, Any]:
        """Get extension status."""
        return get_extension_status()
