"""Self status command for Honcho Pi.

Shows the current status of the Honcho Pi installation including
API service, deriver, database connection, and Pi extension.
"""

import os
import click
import subprocess
from pathlib import Path


def check_systemd_service(service_name):
    """Check if a systemd user service is active."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", service_name],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0, result.stdout.strip()
    except FileNotFoundError:
        return False, "systemctl not found"


def check_api_health(url="http://localhost:8000", timeout=2):
    """Check if Honcho API is responding."""
    try:
        import requests
        response = requests.get(f"{url}/health", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def get_api_version(url="http://localhost:8000", timeout=2):
    """Get Honcho API version if available."""
    try:
        import requests
        response = requests.get(f"{url}/v1/", timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            return data.get("version", "unknown")
    except Exception:
        pass
    return None


def check_pi_extension():
    """Check if Pi extension is installed."""
    pi_ext = Path.home() / ".pi/agent/extensions/honcho.ts"
    settings = Path.home() / ".pi/agent/settings.json"
    
    ext_present = pi_ext.exists()
    settings_present = settings.exists()
    
    enabled = False
    if settings_present:
        try:
            import json
            with open(settings) as f:
                config = json.load(f)
            enabled = config.get("honcho", {}).get("enabled", False)
        except Exception:
            pass
    
    return {
        "present": ext_present,
        "settings_present": settings_present,
        "enabled": enabled,
        "path": str(pi_ext) if ext_present else None,
    }


def check_database(db_url=None):
    """Check database connectivity."""
    from honcho_pi.config import get_db_url
    
    if not db_url:
        try:
            db_url = get_db_url()
        except Exception:
            return False, "No database URL configured"
    
    try:
        import psycopg
        conn = psycopg.connect(db_url, connect_timeout=3)
        conn.execute("SELECT 1")
        conn.close()
        return True, "connected"
    except Exception as e:
        return False, str(e)


@click.command(name="status")
@click.option('--json-output', 'json_output', is_flag=True, help='Output as JSON')
def status(json_output):
    """Show Honcho Pi installation status.
    
    Displays the current state of all components:
    - API service (systemd)
    - Deriver service (systemd)
    - Database connection
    - Pi extension installation
    """
    # Gather status data
    api_active, api_status = check_systemd_service("honcho-api")
    deriver_active, deriver_status = check_systemd_service("honcho-deriver")
    api_healthy = check_api_health() if api_active else False
    api_version = get_api_version() if api_active else None
    db_ok, db_msg = check_database()
    pi = check_pi_extension()
    
    if json_output:
        import json
        output = {
            "api": {
                "service_active": api_active,
                "status": api_status,
                "healthy": api_healthy,
                "version": api_version,
            },
            "deriver": {
                "service_active": deriver_active,
                "status": deriver_status,
            },
            "database": {
                "connected": db_ok,
                "message": db_msg if not db_ok else None,
            },
            "pi_extension": pi,
        }
        click.echo(json.dumps(output, indent=2))
        return
    
    # Human-readable output
    click.echo(click.style("Honcho Pi Status", fg="green", bold=True))
    click.echo("=" * 40)
    
    # API Service
    api_icon = "✓" if api_active else "✗"
    api_color = "green" if api_active else "red"
    click.echo(f"{click.style(api_icon, fg=api_color)} API Service: {api_status}")
    if api_active:
        health_icon = "✓" if api_healthy else "✗"
        health_color = "green" if api_healthy else "yellow"
        click.echo(f"  {click.style(health_icon, fg=health_color)} Health: {'OK' if api_healthy else 'Not responding'}")
        if api_version:
            click.echo(f"  Version: {api_version}")
    
    # Deriver Service
    deriver_icon = "✓" if deriver_active else "✗"
    deriver_color = "green" if deriver_active else "red"
    click.echo(f"{click.style(deriver_icon, fg=deriver_color)} Deriver Service: {deriver_status}")
    
    # Database
    db_icon = "✓" if db_ok else "✗"
    db_color = "green" if db_ok else "red"
    db_status = "connected" if db_ok else db_msg
    click.echo(f"{click.style(db_icon, fg=db_color)} Database: {db_status}")
    
    # Pi Extension
    click.echo()
    click.echo(click.style("Pi Integration", fg="green", bold=True))
    ext_icon = "✓" if pi["present"] else "✗"
    ext_color = "green" if pi["present"] else "yellow"
    click.echo(f"{click.style(ext_icon, fg=ext_color)} Extension: {'installed' if pi['present'] else 'not found'}")
    
    if pi["present"]:
        enabled_icon = "✓" if pi["enabled"] else "⚠"
        enabled_color = "green" if pi["enabled"] else "yellow"
        click.echo(f"{click.style(enabled_icon, fg=enabled_color)} Extension enabled: {pi['enabled']}")
    
    # Summary
    click.echo()
    all_ok = api_active and deriver_active and api_healthy and db_ok and pi["enabled"]
    if all_ok:
        click.echo(click.style("All systems operational ✓", fg="green", bold=True))
    else:
        click.echo(click.style("Some components need attention", fg="yellow"))
        click.echo("Run 'honcho-pi self doctor' for diagnostics.")
