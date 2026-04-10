"""Typer CLI for Honcho Pi."""

import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from honcho_pi.bootstrap import run_configuration, check_prerequisites
from honcho_pi.config import Settings, settings
from honcho_pi.pi_integration import PiIntegration, check_pi_status
from honcho_pi.services import ServiceManager, get_journal_logs

console = Console()
app = typer.Typer(
    name="honcho-pi",
    help="Honcho Pi - Local memory service for Pi-mono",
    rich_markup_mode="rich",
    add_completion=False,
)

# Self management subcommand group (PyApp native)
self_app = typer.Typer(
    name="self",
    help="Self-management commands (PyApp native)",
)
app.add_typer(self_app, name="self")


@app.callback()
def main_callback(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config_dir: Optional[str] = typer.Option(None, "--config-dir", help="Config directory path"),
):
    """Honcho Pi - Local memory service installer and manager."""
    if verbose:
        console.print(f"[dim]Config directory: {settings.config_dir}[/dim]")


@app.command()
def install(
    interactive: bool = typer.Option(True, "--interactive/--non-interactive", help="Run interactively"),
    force: bool = typer.Option(False, "--force", help="Force reconfiguration"),
):
    """Run first-time installation and configuration."""
    console.print(Panel.fit(
        "[bold cyan]Honcho Pi Installation[/bold cyan]",
        border_style="cyan"
    ))
    
    # Check if already configured
    if settings.env_file and settings.env_file.exists() and not force:
        console.print(f"[yellow]Already configured at {settings.env_file}[/yellow]")
        if not interactive or not typer.confirm("Reconfigure?"):
            console.print("Use --force to reconfigure.")
            raise typer.Exit(0)
    
    # Check prerequisites
    console.print("\n[bold]Checking prerequisites...[/bold]")
    issues = check_prerequisites()
    for issue in issues:
        console.print(f"[yellow]⚠ {issue}[/yellow]")
    
    if any("required" in issue.lower() or "Python" in issue for issue in issues):
        console.print("[red]Error: Install cannot continue with missing prerequisites[/red]")
        raise typer.Exit(1)
    
    # Run configuration
    try:
        configured = run_configuration(interactive=interactive, settings=settings)
        
        # Generate service files
        console.print("\n[bold]Setting up services...[/bold]")
        service_mgr = ServiceManager(configured)
        service_mgr.generate_service_files()
        service_mgr.daemon_reload()
        service_mgr.enable_services()
        
        console.print("\n[bold green]✓ Installation complete![/bold green]")
        console.print(f"\nNext steps:")
        console.print(f"  Start services: [bold]honcho-pi start[/bold]")
        console.print(f"  Check status:   [bold]honcho-pi status[/bold]")
        console.print(f"  View logs:      [bold]honcho-pi self logs[/bold]")
        
    except Exception as e:
        console.print(f"[red]Error during installation: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def start(
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for services to be ready"),
):
    """Start Honcho services (API and Deriver)."""
    console.print("[bold]Starting Honcho services...[/bold]")
    
    service_mgr = ServiceManager(settings)
    
    # Generate service files if they don't exist
    service_mgr.generate_service_files()
    service_mgr.daemon_reload()
    
    if service_mgr.start_services():
        console.print("\n[bold green]✓ Services started successfully[/bold green]")
        
        if wait:
            console.print("Waiting for API to be ready...")
            import time
            time.sleep(3)
            
            # Check status
            status = service_mgr.get_status()
            if status.get("api_healthy"):
                console.print(f"[green]✓ API responding at http://{settings.api_host}:{settings.api_port}[/green]")
            else:
                console.print("[yellow]⚠ API not responding yet (may need more time)[/yellow]")
                console.print(f"Check logs: honcho-pi self logs --service honcho-api")
    else:
        console.print("[red]✗ Failed to start services[/red]")
        raise typer.Exit(1)


@app.command()
def stop():
    """Stop Honcho services."""
    console.print("[bold]Stopping Honcho services...[/bold]")
    
    service_mgr = ServiceManager(settings)
    if service_mgr.stop_services():
        console.print("[bold green]✓ Services stopped[/bold green]")
    else:
        console.print("[yellow]⚠ Some services may not have stopped properly[/yellow]")


@app.command()
def restart():
    """Restart Honcho services."""
    console.print("[bold]Restarting Honcho services...[/bold]")
    
    service_mgr = ServiceManager(settings)
    if service_mgr.restart_services():
        console.print("[bold green]✓ Services restarted[/bold green]")
    else:
        console.print("[red]✗ Failed to restart services[/red]")
        raise typer.Exit(1)


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Show status of Honcho services and Pi extension."""
    service_mgr = ServiceManager(settings)
    pi_integration = PiIntegration(settings)
    
    # Service status
    console.print("\n[bold]Service Status[/bold]")
    svc_status = service_mgr.get_status()
    
    table = Table(show_header=False, box=None)
    table.add_column("Service", style="cyan")
    table.add_column("Status", style="green" if svc_status["api_healthy"] else "red")
    
    api_status = "✓ active" if svc_status["honcho-api"] == "active" else f"✗ {svc_status['honcho-api']}"
    deriver_status = "✓ active" if svc_status["honcho-deriver"] == "active" else f"✗ {svc_status['honcho-deriver']}"
    
    table.add_row("honcho-api", api_status)
    table.add_row("honcho-deriver", deriver_status)
    table.add_row("API Health", "✓ responding" if svc_status["api_healthy"] else "✗ not responding")
    
    console.print(table)
    
    # Pi extension status
    console.print("\n[bold]Pi Extension[/bold]")
    pi_status = check_pi_status(settings)
    
    if not pi_status["installed"]:
        console.print("[yellow]⚠ Pi not installed[/yellow]")
    else:
        pi_table = Table(show_header=False, box=None)
        pi_table.add_column("Component", style="cyan")
        pi_table.add_column("Status")
        
        pi_table.add_row(
            "Pi Installation",
            "✓ installed" if pi_status["installed"] else "✗ not found"
        )
        pi_table.add_row(
            "Extension File",
            "✓ present" if pi_status["extension_present"] else "✗ missing"
        )
        pi_table.add_row(
            "Settings Configured",
            "✓ yes" if pi_status["settings_configured"] else "✗ no"
        )
        
        console.print(pi_table)
        
        if pi_status["errors"]:
            for error in pi_status["errors"]:
                console.print(f"[yellow]  ⚠ {error}[/yellow]")
    
    # Configuration info
    if verbose:
        console.print("\n[bold]Configuration[/bold]")
        console.print(f"  Config dir: {settings.config_dir}")
        console.print(f"  Data dir: {settings.data_dir}")
        console.print(f"  API endpoint: http://{settings.api_host}:{settings.api_port}")
        console.print(f"  Database URL: {settings.database_url or 'Not configured'}")


@app.command()
def configure(
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Skip prompts"),
):
    """Re-run configuration wizard."""
    console.print("[bold]Reconfiguring Honcho Pi...[/bold]")
    
    try:
        run_configuration(interactive=not non_interactive, settings=settings)
        console.print("\n[bold green]✓ Configuration updated[/bold green]")
        console.print("Restart services to apply changes: honcho-pi restart")
    except Exception as e:
        console.print(f"[red]Error during configuration: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def doctor(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run diagnostic checks."""
    console.print(Panel.fit(
        "[bold magenta]Honcho Pi Doctor[/bold magenta]",
        border_style="magenta"
    ))
    
    all_good = True
    
    # Python check
    import sys
    py_version = sys.version_info
    if py_version >= (3, 10):
        console.print(f"[green]✓[/green] Python {py_version.major}.{py_version.minor}.{py_version.micro}")
    else:
        console.print(f"[red]✗[/red] Python {py_version.major}.{py_version.minor} (3.10+ required)")
        all_good = False
    
    # PyApp check
    in_pyapp = __file__.startswith(str(Path.home() / ".local" / "share"))
    if in_pyapp:
        console.print("[green]✓[/green] Running in PyApp environment")
    else:
        console.print("[dim]ℹ Running from source (not PyApp)[/dim]")
    
    # Check directories
    for name, path in [
        ("Config directory", settings.config_dir),
        ("Data directory", settings.data_dir),
    ]:
        if path.exists():
            console.print(f"[green]✓[/green] {name}: {path}")
        else:
            console.print(f"[yellow]⚠[/yellow] {name} missing: {path}")
    
    # Check env file
    if settings.env_file and settings.env_file.exists():
        console.print(f"[green]✓[/green] Environment file: {settings.env_file}")
    else:
        console.print(f"[yellow]⚠[/yellow] Environment file not found")
    
    # Service manager
    service_mgr = ServiceManager(settings)
    svc_status = service_mgr.get_status()
    
    console.print("\n[bold]Services[/bold]")
    for svc, status in svc_status.items():
        if svc == "api_healthy":
            continue
        if status == "active":
            console.print(f"[green]✓[/green] {svc}: {status}")
        else:
            console.print(f"[yellow]⚠[/yellow] {svc}: {status}")
            all_good = False
    
    if svc_status.get("api_healthy"):
        console.print(f"[green]✓[/green] API responding to health checks")
    else:
        console.print(f"[yellow]⚠[/yellow] API not responding")
    
    # Database check
    console.print("\n[bold]Database[/bold]")
    if settings.database_url:
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(settings.database_url)
            if parsed.scheme.startswith("postgresql"):
                console.print(f"[green]✓[/green] Database configured: {parsed.scheme}+...")
            else:
                console.print(f"[dim]ℹ Database: {parsed.scheme}[/dim]")
        except Exception:
            console.print(f"[yellow]⚠[/yellow] Database URL may be invalid")
    else:
        console.print(f"[yellow]⚠[/yellow] No database configured")
        all_good = False
    
    # Pi check
    console.print("\n[bold]Pi Integration[/bold]")
    pi_status = check_pi_status(settings)
    if pi_status["installed"]:
        console.print(f"[green]✓[/green] Pi installed")
        if pi_status["extension_present"]:
            console.print(f"[green]✓[/green] Honcho extension present")
        else:
            console.print(f"[yellow]⚠[/yellow] Honcho extension not installed")
            all_good = False
    else:
        console.print(f"[dim]ℹ Pi not installed (optional)[/dim]")
    
    # Summary
    console.print("\n" + "─" * 40)
    if all_good:
        console.print("[bold green]✓ All checks passed![/bold green]")
    else:
        console.print("[bold yellow]⚠ Some issues found[/bold yellow]")
        console.print("Run with --verbose for more details")


# Self Management Commands (PyApp native)

@self_app.command(name="update")
def self_update(
    check: bool = typer.Option(False, "--check", help="Check for updates only"),
):
    """Check for and install updates (PyApp native)."""
    console.print("[bold]Checking for updates...[/bold]")
    
    import urllib.request
    import json
    
    try:
        # Check GitHub releases
        url = "https://api.github.com/repos/dsidlo/honcho-pi/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read())
            latest_version = data["tag_name"].lstrip("v")
            
            from honcho_pi import __version__
            current_version = __version__
            
            if latest_version > current_version:
                console.print(f"[green]Update available: {current_version} → {latest_version}[/green]")
                
                if check:
                    return
                
                if typer.confirm("Download and install update?"):
                    console.print("[dim]Downloading update...[/dim]")
                    # Implementation would download and replace binary
                    console.print("[yellow]Auto-update not yet implemented[/yellow]")
                    console.print(f"Download manually: {data['html_url']}")
            else:
                console.print(f"[green]✓ Already up to date ({current_version})[/green]")
                
    except Exception as e:
        console.print(f"[yellow]Could not check for updates: {e}[/yellow]")
        console.print("Visit: https://github.com/dsidlo/honcho-pi/releases")


@self_app.command(name="metadata")
def self_metadata():
    """Show version and metadata (PyApp native)."""
    from honcho_pi import __version__
    
    console.print(f"[bold]Honcho Pi[/bold] v{__version__}")
    console.print(f"Config dir: {settings.config_dir}")
    console.print(f"Data dir: {settings.data_dir}")
    console.print(f"Install dir: {settings.honcho_install_dir}")
    
    # PyApp specific
    if pyapp_install := Settings.from_env().honcho_install_dir:
        console.print(f"PyApp install: {pyapp_install}")


@self_app.command(name="logs")
def self_logs(
    service: str = typer.Option("honcho-api", "--service", help="Service to view logs for"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """View service logs."""
    get_journal_logs(service, lines=lines, follow=follow)


@self_app.command(name="uninstall")
def self_uninstall(
    confirm: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
):
    """Uninstall Honcho Pi."""
    if not confirm:
        console.print("[bold red]This will remove:[/bold red]")
        console.print(f"  - Services: honcho-api, honcho-deriver")
        console.print(f"  - Configuration: {settings.config_dir}")
        console.print(f"  - Data: {settings.data_dir}")
        console.print(f"  - Installation: {settings.honcho_install_dir}")
        
        if not typer.confirm("Are you sure?"):
            console.print("Aborted.")
            raise typer.Exit(0)
    
    # Stop and remove services
    service_mgr = ServiceManager(settings)
    service_mgr.uninstall()
    
    # Remove Pi extension
    try:
        from honcho_pi.pi_integration import uninstall_pi_extension
        uninstall_pi_extension(settings)
    except Exception:
        pass
    
    # Remove directories
    import shutil
    for path in [settings.config_dir, settings.data_dir, settings.honcho_install_dir]:
        if path.exists():
            shutil.rmtree(path)
            console.print(f"[dim]Removed: {path}[/dim]")
    
    console.print("[green]✓ Honcho Pi uninstalled[/green]")
    console.print("[dim]Note: The honcho-pi binary remains. Remove it manually if desired.[/dim]")


if __name__ == "__main__":
    app()