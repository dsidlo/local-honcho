"""Systemd service management for Honcho Pi."""

import os
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

from honcho_pi.config import Settings

console = Console()


class ServiceError(Exception):
    """Service management error."""
    pass


# Service template for honcho-api
HONCHO_API_SERVICE_TEMPLATE = """[Unit]
Description=Honcho API Server
Documentation=https://docs.honcho.dev
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
WorkingDirectory={workdir}
ExecStart={uv_path} run --no-dev fastapi dev {main_py} --host {host} --port {port}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile={env_file}
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3
TimeoutStopSec=30
KillSignal=SIGTERM

[Install]
WantedBy=default.target
"""

# Service template for honcho-deriver
HONCHO_DERIVER_SERVICE_TEMPLATE = """[Unit]
Description=Honcho Deriver (Background Worker)
Documentation=https://docs.honcho.dev
After=network-online.target honcho-api.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={workdir}
ExecStart={uv_path} run --no-dev python -m {deriver_module}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile={env_file}
Restart=always
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3
TimeoutStopSec=30
KillSignal=SIGTERM

[Install]
WantedBy=default.target
"""


class ServiceManager:
    """Manages systemd user services for Honcho."""
    
    def __init__(self, settings: Optional[Settings] = None):
        """Initialize service manager.
        
        Args:
            settings: Honcho Pi settings instance
        """
        self.settings = settings or Settings.from_env()
        self.service_dir = Path.home() / ".config" / "systemd" / "user"
        
        # Check if running in PyApp environment
        self.pyapp_enabled = os.getenv("PYAPP") == "1"
        if self.pyapp_enabled:
            self.install_dir = Path(os.getenv("INSTALL_DIR_HONCHO_PI", ""))
        else:
            self.install_dir = self.settings.honcho_install_dir
    
    def _get_uv_path(self) -> str:
        """Get the path to UV executable."""
        # Try to find UV in common locations
        for path in [
            Path.home() / ".cargo" / "bin" / "uv",
            Path.home() / ".local" / "bin" / "uv",
            Path("/usr" / "local" / "bin" / "uv"),
            Path("/usr" / "bin" / "uv"),
        ]:
            if path.exists():
                return str(path)
        
        # Try to find in PATH
        try:
            result = subprocess.run(
                ["which", "uv"],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "uv"  # Fall back to PATH resolution
    
    def _get_service_status(self, service: str) -> str:
        """Get status of a service."""
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", service],
                capture_output=True,
                text=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "unknown"
    
    def generate_service_files(self) -> tuple[Path, Path]:
        """Generate systemd service files.
        
        Returns:
            Tuple of (api_service_path, deriver_service_path)
        """
        self.service_dir.mkdir(parents=True, exist_ok=True)
        
        # Find paths
        uv_path = self._get_uv_path()
        workdir = self.settings.honcho_source_dir or Path.cwd()
        main_py = workdir / "src" / "main.py"
        deriver_module = "src.deriver"
        env_file = self.settings.env_file or (self.settings.config_dir / ".env")
        
        # Find first-run bootstrap if in PyApp
        if self.pyapp_enabled and (workdir / "bootstrap.py").exists():
            # PyApp environment: find main.py differently
            main_py = workdir / "main.py"
            deriver_module = "deriver"
        
        # Generate API service
        api_content = HONCHO_API_SERVICE_TEMPLATE.format(
            workdir=str(workdir),
            uv_path=uv_path,
            main_py=str(main_py),
            host=self.settings.api_host,
            port=self.settings.api_port,
            env_file=str(env_file),
        )
        
        api_service_path = self.service_dir / "honcho-api.service"
        api_service_path.write_text(api_content)
        api_service_path.chmod(0o644)
        
        # Generate Deriver service
        deriver_content = HONCHO_DERIVER_SERVICE_TEMPLATE.format(
            workdir=str(workdir),
            uv_path=uv_path,
            deriver_module=deriver_module,
            env_file=str(env_file),
        )
        
        deriver_service_path = self.service_dir / "honcho-deriver.service"
        deriver_service_path.write_text(deriver_content)
        deriver_service_path.chmod(0o644)
        
        console.print(f"[dim]Created: {api_service_path}[/dim]")
        console.print(f"[dim]Created: {deriver_service_path}[/dim]")
        
        return api_service_path, deriver_service_path
    
    def daemon_reload(self) -> bool:
        """Reload systemd daemon."""
        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=True,
                capture_output=True
            )
            return True
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to reload daemon: {e}[/red]")
            return False
    
    def enable_services(self) -> bool:
        """Enable services to start on boot."""
        try:
            for service in ["honcho-api", "honcho-deriver"]:
                subprocess.run(
                    ["systemctl", "--user", "enable", service],
                    check=True,
                    capture_output=True
                )
            console.print("[green]✓ Services enabled for auto-start[/green]")
            return True
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to enable services: {e}[/red]")
            return False
    
    def start_services(self) -> bool:
        """Start services."""
        try:
            # Start API first
            subprocess.run(
                ["systemctl", "--user", "start", "honcho-api"],
                check=True,
                capture_output=True
            )
            console.print("[green]✓ honcho-api started[/green]")
            
            # Wait a moment for API to be ready
            import time
            time.sleep(2)
            
            # Then start deriver
            subprocess.run(
                ["systemctl", "--user", "start", "honcho-deriver"],
                check=True,
                capture_output=True
            )
            console.print("[green]✓ honcho-deriver started[/green]")
            
            return True
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to start services: {e}[/red]")
            return False
    
    def stop_services(self) -> bool:
        """Stop services."""
        try:
            for service in ["honcho-deriver", "honcho-api"]:
                subprocess.run(
                    ["systemctl", "--user", "stop", service],
                    capture_output=True
                )
                console.print(f"[dim]Stopped: {service}[/dim]")
            return True
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to stop services: {e}[/red]")
            return False
    
    def restart_services(self) -> bool:
        """Restart services."""
        try:
            for service in ["honcho-api", "honcho-deriver"]:
                subprocess.run(
                    ["systemctl", "--user", "restart", service],
                    check=True,
                    capture_output=True
                )
            console.print("[green]✓ Services restarted[/green]")
            return True
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to restart services: {e}[/red]")
            return False
    
    def get_status(self) -> dict:
        """Get status of all services.
        
        Returns:
            Dictionary with service status information
        """
        status = {
            "honcho-api": self._get_service_status("honcho-api"),
            "honcho-deriver": self._get_service_status("honcho-deriver"),
        }
        
        # Check if API is actually responding
        try:
            import urllib.request
            url = f"http://{self.settings.api_host}:{self.settings.api_port}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as response:
                status["api_healthy"] = response.status == 200
        except Exception:
            status["api_healthy"] = False
        
        return status
    
    def install_and_start(self) -> bool:
        """Full installation: generate files, reload, enable, and start."""
        # Generate service files
        self.generate_service_files()
        
        # Reload daemon
        if not self.daemon_reload():
            return False
        
        # Enable services
        if not self.enable_services():
            return False
        
        # Start services
        return self.start_services()
    
    def uninstall(self) -> bool:
        """Remove services."""
        try:
            # Stop and disable
            for service in ["honcho-deriver", "honcho-api"]:
                subprocess.run(
                    ["systemctl", "--user", "disable", service],
                    capture_output=True
                )
                subprocess.run(
                    ["systemctl", "--user", "stop", service],
                    capture_output=True
                )
            
            # Remove service files
            for service_file in [
                self.service_dir / "honcho-api.service",
                self.service_dir / "honcho-deriver.service"
            ]:
                if service_file.exists():
                    service_file.unlink()
            
            self.daemon_reload()
            console.print("[green]✓ Services uninstalled[/green]")
            return True
        except Exception as e:
            console.print(f"[red]Failed to uninstall services: {e}[/red]")
            return False


def get_journal_logs(service: str, lines: int = 50, follow: bool = False) -> None:
    """View journal logs for a service.
    
    Args:
        service: Service name (honcho-api or honcho-deriver)
        lines: Number of lines to show
        follow: Whether to follow logs
    """
    cmd = ["journalctl", "--user", "-u", service, f"-n{lines}"]
    if follow:
        cmd.append("-f")
    
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        pass