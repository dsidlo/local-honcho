"""Systemd service management for honcho-pi with Jinja2 templates."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, Template

from honcho_pi.config import (
    get_config_dir,
    get_install_dir,
)


def get_service_dir() -> Path:
    """Get directory for storing generated systemd service files."""
    service_dir = get_config_dir() / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    return service_dir


def get_package_data_dir() -> Path:
    """Get the package data directory.
    
    Works both in development and when bundled with PyInstaller.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # PyInstaller sets _MEIPASS to extracted bundle directory
        base_path = Path(sys._MEIPASS)
        return base_path / "honcho_pi" / "package_data"
    else:
        # Development: use package-relative path
        package_dir = Path(__file__).parent
        return package_dir / "package_data"


def render_template(template_path: Path, context: dict) -> str:
    """Render a Jinja2 template with context."""
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    
    with open(template_path) as f:
        template = Template(f.read())
    
    return template.render(context)


def get_template_dir() -> Path:
    """Get template directory (works with PyInstaller bundled data)."""
    pkg_data_dir = get_package_data_dir()
    
    templates_dir = pkg_data_dir / "systemd"
    if templates_dir.exists():
        return templates_dir
    
    # Fallback: check relative to install dir
    install_dir = get_install_dir()
    templates_dir = install_dir / "services" / "templates"
    
    if templates_dir.exists():
        return templates_dir
    
    raise FileNotFoundError(f"Service templates directory not found")



def generate_services() -> None:
    """Generate systemd service files from templates."""
    
    service_dir = get_service_dir()
    service_dir.mkdir(parents=True, exist_ok=True)
    
    # Get template directory
    template_dir = get_template_dir()
    
    # Build context for templates
    context = {
        "project_name": "honcho-pi",
        "version": "1.0.0",
        "install_dir": str(get_install_dir()),
        "config_dir": str(get_config_dir()),
        "pyapp_python": "python",  # PyApp sets up python in PATH
        "uv_command": _get_uv_path(),
        "api_host": "0.0.0.0",
        "api_port": os.environ.get("API_PORT", "8000"),
    }
    
    # Service files to generate
    services = [
        ("honcho-api.service", "honcho-api.service"),
        ("honcho-deriver.service", "honcho-deriver.service"),
    ]
    
    for template_name, output_name in services:
        template_path = template_dir / template_name
        output_path = service_dir / output_name
        
        rendered = render_template(template_path, context)
        
        with open(output_path, "w") as f:
            f.write(rendered)
        
        os.chmod(output_path, 0o644)


def generate_docker_compose(config: dict) -> Path:
    """Generate docker-compose file for database."""
    template_dir = get_template_dir()
    template_path = template_dir / "docker-compose-db.yml"
    
    # Get user for default postgres password
    import getpass
    default_password = "honcho_default"
    
    # Build context
    context = {
        "project_name": "honcho-pi",
        "version": "1.0.0",
        "postgres_image": config.get("postgres_image", "ankane/pgvector:latest"),
        "postgres_container_name": config.get("postgres_container_name", "honcho-pi-db"),
        "postgres_user": config.get("postgres_user", "honcho"),
        "postgres_password": config.get("postgres_password", default_password),
        "postgres_db": config.get("postgres_db", "honcho"),
        "postgres_port": config.get("postgres_port", "5432"),
        "postgres_volume_name": config.get("postgres_volume_name", "honcho_pgdata"),
        "network_name": config.get("network_name", "honcho-network"),
        "enable_redis": config.get("enable_redis", False),
        "redis_version": config.get("redis_version", "7-alpine"),
        "redis_container_name": config.get("redis_container_name", "honcho-pi-redis"),
        "redis_port": config.get("redis_port", "6379"),
        "redis_volume_name": config.get("redis_volume_name", "honcho_redis"),
    }
    
    rendered = render_template(template_path, context)
    
    # Save to config directory
    output_path = get_config_dir() / "docker-compose-db.yml"
    with open(output_path, "w") as f:
        f.write(rendered)
    
    return output_path


def _get_uv_path() -> str:
    """Get UV executable path."""
    try:
        result = subprocess.run(
            ["which", "uv"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    
    # Check common locations
    for path in ["~/.cargo/bin/uv", "~/.local/bin/uv", "/usr/local/bin/uv"]:
        expanded = os.path.expanduser(path)
        if os.path.exists(expanded):
            return expanded
    
    return "uv"


def enable_services(services: Optional[list[str]] = None) -> None:
    """Enable systemd services."""
    services = services or ["api", "deriver"]
    
    for svc in services:
        service_name = f"honcho-{svc}.service"
        try:
            subprocess.run(
                ["systemctl", "--user", "enable", service_name],
                check=True,
                capture_output=True,
                timeout=10
            )
        except subprocess.CalledProcessError as e:
            print(f"Warning: Could not enable {service_name}: {e}")


def disable_services(services: Optional[list[str]] = None) -> None:
    """Disable systemd services."""
    services = services or ["api", "deriver"]
    
    for svc in services:
        service_name = f"honcho-{svc}.service"
        try:
            subprocess.run(
                ["systemctl", "--user", "disable", service_name],
                check=False,
                capture_output=True,
                timeout=10
            )
        except Exception:
            pass


def start_services(services: Optional[list[str]] = None) -> None:
    """Start systemd services."""
    services = services or ["api", "deriver"]
    
    # Reload daemon first
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
        timeout=10
    )
    
    for svc in services:
        service_name = f"honcho-{svc}.service"
        try:
            subprocess.run(
                ["systemctl", "--user", "start", service_name],
                check=True,
                timeout=15
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to start {service_name}: {e}")


def stop_services(services: Optional[list[str]] = None) -> None:
    """Stop systemd services."""
    services = services or ["api", "deriver"]
    
    for svc in services:
        service_name = f"honcho-{svc}.service"
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", service_name],
                check=False,
                capture_output=True,
                timeout=15
            )
        except Exception:
            pass


def check_service_status(service: str) -> dict:
    """Check the status of a service."""
    service_name = f"honcho-{service}.service"
    
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            # Get more details
            detail_result = subprocess.run(
                ["systemctl", "--user", "show", service_name, 
                 "--property=MainPID,ActiveState,SubState"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            details = {}
            for line in detail_result.stdout.split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    details[key] = value
            
            return {
                "running": True,
                "status": "active",
                "details": f"PID {details.get('MainPID', 'N/A')}",
            }
        else:
            return {
                "running": False,
                "status": result.stdout.strip() or "inactive",
                "error": f"Service is {result.stdout.strip() or 'inactive'}",
            }
    except FileNotFoundError:
        return {
            "running": False,
            "status": "unknown",
            "error": "systemctl not found",
        }
    except Exception as e:
        return {
            "running": False,
            "status": "error",
            "error": str(e),
        }


# Compatibility wrapper for bootstrap.py
class ServiceManager:
    """Service manager wrapper for compatibility."""
    
    def __init__(self):
        self.service_dir = get_service_dir()
    
    def generate_services(self) -> None:
        """Generate systemd service files."""
        generate_services()
    
    def start(self, services: Optional[list[str]] = None) -> None:
        """Start services."""
        start_services(services)
    
    def stop(self, services: Optional[list[str]] = None) -> None:
        """Stop services."""
        stop_services(services)
    
    def status(self, service: str) -> dict:
        """Check service status."""
        return check_service_status(service)
