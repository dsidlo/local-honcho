"""Self doctor command for Honcho Pi.

Runs diagnostic checks to verify installation health and identify issues.
"""

import os
import sys
import click
import subprocess
from pathlib import Path


class CheckResult:
    """Result of a diagnostic check."""
    def __init__(self, name, passed, message="", advice=""):
        self.name = name
        self.passed = passed
        self.message = message
        self.advice = advice


def check_pyapp_env():
    """Check PyApp environment variables."""
    is_pyapp = os.environ.get("PYAPP") == "1"
    if not is_pyapp:
        return CheckResult(
            "PyApp Environment",
            False,
            "Not running in PyApp environment",
            "This is fine for development, but production should use PyApp binary"
        )
    
    cmd_name = os.environ.get("PYAPP_COMMAND_NAME", "unknown")
    return CheckResult(
        "PyApp Environment",
        True,
        f"Running as PyApp binary: {cmd_name}"
    )


def check_installation():
    """Check if PyApp installation exists."""
    install_dir = os.environ.get("INSTALL_DIR_HONCHO_PI", "")
    
    if not install_dir:
        # Try to find installation
        import glob
        pattern = str(Path.home() / ".local/share/honcho-pi/*/")
        matches = glob.glob(pattern)
        if matches:
            install_dir = matches[0]
    
    if install_dir and Path(install_dir).exists():
        return CheckResult(
            "Installation Directory",
            True,
            f"Found at {install_dir}"
        )
    
    return CheckResult(
        "Installation Directory",
        False,
        "Installation not found",
        "Run 'honcho-pi self install' to complete setup"
    )


def check_python():
    """Check Python runtime."""
    version = sys.version_info
    if version.major == 3 and version.minor >= 10:
        return CheckResult(
            "Python Runtime",
            True,
            f"Python {version.major}.{version.minor}.{version.micro}"
        )
    return CheckResult(
        "Python Runtime",
        False,
        f"Python {version.major}.{version.minor} (requires 3.10+)",
        "Update Python or reinstall with PyApp"
    )


def check_uv():
    """Check UV installation."""
    uv_path = Path.home() / ".cargo/bin/uv"
    if uv_path.exists():
        try:
            result = subprocess.run([str(uv_path), "--version"], capture_output=True, text=True)
            version = result.stdout.strip().split()[-1] if result.stdout else "unknown"
            return CheckResult(
                "UV",
                True,
                f"Installed: {version}"
            )
        except Exception as e:
            return CheckResult(
                "UV",
                False,
                f"Error checking UV: {e}"
            )
    
    # Check if uv is in PATH
    try:
        result = subprocess.run(["uv", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            version = result.stdout.strip().split()[-1]
            return CheckResult(
                "UV",
                True,
                f"Installed: {version}"
            )
    except FileNotFoundError:
        pass
    
    return CheckResult(
        "UV",
        False,
        "UV not found",
        "Install from https://astral.sh/uv or reinstall honcho-pi"
    )


def check_config():
    """Check configuration file."""
    config_paths = [
        Path.home() / ".config/honcho-pi/.env",
        Path.home() / ".honcho-pi/.env",
    ]
    
    for config_path in config_paths:
        if config_path.exists():
            return CheckResult(
                "Configuration File",
                True,
                f"Found at {config_path}"
            )
    
    return CheckResult(
        "Configuration File",
        False,
        "No configuration file found",
        "Run 'honcho-pi self configure' to create one"
    )


def check_dependencies():
    """Check if required dependencies are installed."""
    required = [
        ("click", "click"),
        ("pydantic", "pydantic"),
        ("rich", "rich"),
    ]
    
    missing = []
    versions = {}
    
    for name, module in required:
        try:
            mod = __import__(module)
            versions[name] = getattr(mod, "__version__", "unknown")
        except ImportError:
            missing.append(name)
    
    if missing:
        return CheckResult(
            "Dependencies",
            False,
            f"Missing: {', '.join(missing)}",
            "Reinstall honcho-pi or check PyApp installation"
        )
    
    return CheckResult(
        "Dependencies",
        True,
        f"All required packages present ({len(versions)} packages)"
    )


def check_services():
    """Check systemd services."""
    try:
        # Check if systemd is available
        result = subprocess.run(
            ["systemctl", "--version"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return CheckResult(
                "Systemd Services",
                False,
                "systemctl not available",
                "Systemd is required for service management"
            )
        
        # Check for services
        services = ["honcho-api", "honcho-deriver"]
        states = {}
        
        for svc in services:
            svc_file = Path.home() / ".config/systemd/user" / f"{svc}.service"
            states[svc] = {
                "file": svc_file.exists(),
                "active": False
            }
            
            if svc_file.exists():
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", svc],
                    capture_output=True,
                    text=True
                )
                states[svc]["active"] = result.returncode == 0
        
        all_present = all(s["file"] for s in states.values())
        all_active = all(s["active"] for s in states.values())
        
        if all_present and all_active:
            return CheckResult(
                "Systemd Services",
                True,
                "All services present and running"
            )
        elif all_present:
            return CheckResult(
                "Systemd Services",
                False,
                "Services present but not all running",
                "Start with: systemctl --user start honcho-api honcho-deriver"
            )
        else:
            return CheckResult(
                "Systemd Services",
                False,
                "Service files not installed",
                "Run 'honcho-pi self configure' to create services"
            )
            
    except Exception as e:
        return CheckResult(
            "Systemd Services",
            False,
            f"Error checking services: {e}"
        )


def check_database_connection():
    """Check database connectivity."""
    from honcho_pi.config import get_db_url
    
    try:
        db_url = get_db_url()
    except Exception:
        return CheckResult(
            "Database Connection",
            False,
            "No database URL configured",
            "Set DATABASE_URL in ~/.config/honcho-pi/.env"
        )
    
    try:
        import psycopg
        conn = psycopg.connect(db_url, connect_timeout=3)
        
        # Check pgvector
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            has_vector = cur.fetchone() is not None
        
        conn.close()
        
        if has_vector:
            return CheckResult(
                "Database Connection",
                True,
                "Connected with pgvector enabled"
            )
        else:
            return CheckResult(
                "Database Connection",
                False,
                "Connected but pgvector extension missing",
                "Run: CREATE EXTENSION vector;"
            )
            
    except Exception as e:
        return CheckResult(
            "Database Connection",
            False,
            f"Connection failed: {e}",
            "Check database URL and ensure PostgreSQL is running"
        )


def check_api_health():
    """Check API health."""
    from honcho_pi.config import get_api_url
    
    try:
        import requests
        url = get_api_url()
        response = requests.get(f"{url}/health", timeout=2)
        
        if response.status_code == 200:
            return CheckResult(
                "API Health",
                True,
                f"API responding on {url}"
            )
        else:
            return CheckResult(
                "API Health",
                False,
                f"API returned status {response.status_code}",
                "Check API logs: honcho-pi self logs"
            )
    except Exception as e:
        return CheckResult(
            "API Health",
            False,
            f"{e}",
            "Ensure honcho-api service is running"
        )


def check_pi_extension():
    """Check Pi extension."""
    ext_path = Path.home() / ".pi/agent/extensions/honcho.ts"
    settings_path = Path.home() / ".pi/agent/settings.json"
    
    present = ext_path.exists()
    
    if not present:
        return CheckResult(
            "Pi Extension",
            False,
            "Extension not installed",
            "Run 'honcho-pi self configure' and enable Pi integration"
        )
    
    # Check settings
    enabled = False
    if settings_path.exists():
        try:
            import json
            with open(settings_path) as f:
                settings = json.load(f)
            enabled = settings.get("honcho", {}).get("enabled", False)
        except Exception:
            pass
    
    if enabled:
        return CheckResult(
            "Pi Extension",
            True,
            "Installed and enabled"
        )
    else:
        return CheckResult(
            "Pi Extension",
            False,
            "Installed but not enabled",
            "Enable in ~/.pi/agent/settings.json: {'honcho': {'enabled': true}}"
        )


def check_permissions():
    """Check file permissions."""
    issues = []
    
    # Check home directory
    home = Path.home()
    if not os.access(home, os.W_OK):
        issues.append(f"No write access to {home}")
    
    # Check config directory
    config_dir = home / ".config"
    if config_dir.exists() and not os.access(config_dir, os.W_OK):
        issues.append(f"No write access to {config_dir}")
    
    # Check if running as root (discouraged)
    if os.geteuid() == 0:
        issues.append("Running as root (not recommended)")
    
    if issues:
        return CheckResult(
            "Permissions",
            False,
            f"Issues found: {', '.join(issues)}",
            "Run as regular user with appropriate group membership"
        )
    
    return CheckResult(
        "Permissions",
        True,
        "All checks passed"
    )


@click.command(name="doctor")
@click.option('--fix', is_flag=True, help='Attempt to fix issues automatically')
@click.option('--json-output', 'json_output', is_flag=True, help='Output as JSON')
def doctor(fix, json_output):
    """Run diagnostic checks.
    
    Performs comprehensive health checks on the Honcho Pi installation:
    - PyApp environment
    - Python runtime and dependencies
    - Database connectivity
    - API health
    - Pi extension
    - Systemd services
    - File permissions
    """
    checks = [
        check_pyapp_env(),
        check_installation(),
        check_python(),
        check_uv(),
        check_config(),
        check_dependencies(),
        check_permissions(),
        check_services(),
        check_database_connection(),
        check_api_health(),
        check_pi_extension(),
    ]
    
    if json_output:
        import json
        output = {
            "summary": {
                "total": len(checks),
                "passed": sum(1 for c in checks if c.passed),
                "failed": sum(1 for c in checks if not c.passed),
            },
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "advice": c.advice,
                }
                for c in checks
            ]
        }
        click.echo(json.dumps(output, indent=2))
        return
    
    # Human-readable output
    click.echo(click.style("Honcho Pi Diagnostics", fg="green", bold=True))
    click.echo("=" * 50)
    
    passed = 0
    failed = 0
    
    for check in checks:
        if check.passed:
            icon = "✓"
            color = "green"
            passed += 1
        else:
            icon = "✗"
            color = "red"
            failed += 1
        
        click.echo()
        click.echo(f"{click.style(icon, fg=color)} {click.style(check.name, bold=True)}")
        click.echo(f"  {check.message}")
        
        if not check.passed and check.advice:
            click.echo(f"  {click.style('→', fg='yellow')} {check.advice}")
    
    # Summary
    click.echo()
    click.echo("=" * 50)
    if failed == 0:
        click.echo(click.style(f"All {passed} checks passed! ✓", fg="green", bold=True))
    else:
        click.echo(click.style(f"{failed} of {len(checks)} checks failed", fg="yellow", bold=True))
        
        if fix:
            click.echo()
            click.echo("Attempting automatic fixes...")
            # Implement auto-fix logic here
            click.echo("(Auto-fix not yet implemented; manual intervention required)")


# Add shortcut alias
@click.command(name="diagnose")
def diagnose():
    """Alias for doctor command."""
    return doctor.callback(fix=False, json_output=False)
