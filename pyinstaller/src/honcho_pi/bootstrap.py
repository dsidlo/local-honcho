"""First-run configuration and bootstrap logic.

Handles interactive setup, database initialization, and system configuration.
"""

import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, IntPrompt, Prompt

from honcho_pi.config import (
    find_env_file,
    load_env_file,
    get as get_config,
    get_db_url,
    get_api_url,
    get_config_dir,
    ensure_config_dir,
    is_pyapp,
    get_pyapp_info,
    DEFAULTS,
)
from honcho_pi.services import (
    ServiceManager,
    generate_services,
    generate_docker_compose,
    start_services,
    stop_services,
    check_service_status,
)
from honcho_pi.pi_integration import PiExtensionManager

console = Console()


def run_configuration(
    interactive: bool = True,
    force: bool = False,
) -> None:
    """Run the configuration wizard.
    
    Args:
        interactive: If False, use defaults without prompting
        force: Force reconfiguration even if already configured
    """
    settings = get_settings()
    config = ConfigManager()
    
    # Check if already configured
    env_file = settings.config_dir / ".env"
    if env_file.exists() and not force:
        console.print("[yellow]Honcho Pi is already configured.[/]")
        if interactive:
            reconfigure = Confirm.ask("Reconfigure?", default=False)
            if not reconfigure:
                console.print("[dim]Configuration cancelled.[/]")
                return
        else:
            console.print("[dim]Use --force to reconfigure.[/]")
            return
    
    # Ensure directories exist
    settings.ensure_directories()
    
    # Run setup steps
    _configure_database(interactive)
    _configure_llm(interactive)
    _configure_embedding(interactive)
    _configure_reranker(interactive)
    _configure_api(interactive)
    _configure_pi_extension(interactive)
    _configure_features(interactive)
    
    # Save configuration
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Saving configuration...", total=None)
        config.save_env_file({})
        progress.update(task, completed=True)
    
    console.print("[green]✓ Configuration saved to[/]", str(settings.config_dir / ".env"))
    
    # Install Pi extension if enabled
    if settings.pi_extension_enabled:
        _install_pi_extension()
    
    # Generate systemd services
    _generate_systemd_services()
    
    # Finalize
    console.print("\n[bold]Next steps:[/]")
    console.print("  1. Start services: [cyan]honcho-pi start[/]")
    console.print("  2. Check status:   [cyan]honcho-pi status[/]")
    console.print("  3. View logs:      [cyan]honcho-pi logs --follow[/]")


def _configure_database(interactive: bool) -> None:
    """Configure database settings."""
    settings = get_settings()
    
    console.print(Panel("[bold blue]Database Configuration[/]", border_style="blue"))
    
    if interactive:
        # Docker preference
        use_docker = Confirm.ask(
            "Use Docker Postgres with pgvector?",
            default=settings.database_use_docker
        )
        settings.database_use_docker = use_docker
        
        if use_docker:
            console.print("[dim]Docker will be used for database.[/]")
            # Set default Docker settings
            settings.database_host = "localhost"
            settings.database_port = 5432
            settings.database_name = "honcho"
            settings.database_user = "postgres"
            settings.database_password = "password"
            console.print("[yellow]⚠ Using default Docker credentials (postgres/password)[/]")
            console.print("   Change these in production!")
        else:
            # Custom database URL
            db_url = Prompt.ask(
                "Enter PostgreSQL URI",
                default=settings.get_database_url()
            )
            settings.database_url = db_url
    
    # Test connection if not using Docker
    if interactive and not settings.database_use_docker:
        console.print("[dim]Testing database connection...[/]")
        try:
            _test_database_connection(settings.get_database_url())
            console.print("[green]✓ Database connection successful[/]")
        except Exception as e:
            console.print(f"[red]✗ Database connection failed: {e}[/]")
            console.print("[yellow]Continuing anyway - you can fix this later.[/]")


def _configure_llm(interactive: bool) -> None:
    """Configure LLM settings."""
    settings = get_settings()
    
    console.print(Panel("[bold blue]LLM Configuration[/]", border_style="blue"))
    
    if interactive:
        provider = Prompt.ask(
            "LLM Provider",
            choices=["anthropic", "openai", "groq", "gemini"],
            default=settings.llm_provider
        )
        settings.llm_provider = provider
        
        # Check for existing API key
        import os
        env_key = f"{provider.upper()}_API_KEY"
        existing_key = os.environ.get(env_key) or os.environ.get(f"LLM_{env_key}")
        
        if existing_key:
            console.print(f"[green]✓ Found existing {env_key} in environment[/]")
            settings.llm_api_key = existing_key
        else:
            api_key = Prompt.ask(
                f"{provider.title()} API key",
                password=True
            )
            if api_key:
                settings.llm_api_key = api_key
                console.print("[green]✓ API key configured[/]")
        
        model = Prompt.ask(
            "LLM Model",
            default=settings.llm_model
        )
        settings.llm_model = model


def _configure_embedding(interactive: bool) -> None:
    """Configure embedding settings."""
    settings = get_settings()
    
    console.print(Panel("[bold blue]Embedding Configuration[/]", border_style="blue"))
    
    if interactive:
        use_ollama = Confirm.ask(
            "Use local Ollama for embeddings?",
            default=settings.embedding_use_ollama
        )
        settings.embedding_use_ollama = use_ollama
        
        if use_ollama:
            settings.embedding_provider = "ollama"
            ollama_model = Prompt.ask(
                "Ollama embedding model",
                default=settings.embedding_ollama_model
            )
            settings.embedding_ollama_model = ollama_model
            settings.embedding_model = ollama_model
            console.print(f"[dim]Ensure Ollama is running and {ollama_model} is pulled.[/]")
        else:
            settings.embedding_provider = "openai"
            api_key = Prompt.ask(
                "OpenAI API key (for embeddings)",
                password=True
            )
            if api_key:
                settings.embedding_api_key = api_key
            settings.embedding_model = "text-embedding-ada-002"


def _configure_reranker(interactive: bool) -> None:
    """Configure reranker settings."""
    settings = get_settings()
    
    console.print(Panel("[bold blue]Reranker Configuration[/]", border_style="blue"))
    
    if interactive:
        enabled = Confirm.ask(
            "Enable reranker?",
            default=settings.reranker_enabled
        )
        settings.reranker_enabled = enabled
        
        if enabled:
            use_ollama = Confirm.ask(
                "Use local Ollama for reranker?",
                default=settings.reranker_use_ollama
            )
            settings.reranker_use_ollama = use_ollama
            
            if use_ollama:
                console.print(f"[dim]Ensure model {settings.reranker_model} is pulled.[/]")
                console.print("  ollama pull qllama/bge-reranker-large:f16")


def _configure_api(interactive: bool) -> None:
    """Configure API settings."""
    settings = get_settings()
    
    console.print(Panel("[bold blue]API Configuration[/]", border_style="blue"))
    
    if interactive:
        port = IntPrompt.ask(
            "API port",
            default=settings.api_port
        )
        settings.api_port = port
        settings.api_url = f"http://{settings.api_host}:{port}"
        
        # Check if port is available
        if not _is_port_available(port):
            console.print(f"[yellow]⚠ Port {port} may already be in use[/]")


def _configure_pi_extension(interactive: bool) -> None:
    """Configure Pi extension settings."""
    settings = get_settings()
    
    console.print(Panel("[bold blue]Pi Extension Configuration[/]", border_style="blue"))
    
    if interactive:
        enabled = Confirm.ask(
            "Enable Pi extension integration?",
            default=settings.pi_extension_enabled
        )
        settings.pi_extension_enabled = enabled
        
        if enabled:
            # Check Pi installation
            pi_manager = PiExtensionManager()
            if not pi_manager.is_pi_installed():
                console.print("[yellow]⚠ Pi not found at ~/.pi/agent/[/]")
                console.print("Install Pi first: https://github.com/mariozechner/pi")
                settings.pi_extension_enabled = False
            else:
                settings.pi_extension_hooks = Confirm.ask(
                    "Enable observation hooks?",
                    default=settings.pi_extension_hooks
                )
                settings.pi_extension_git_branch = Confirm.ask(
                    "Enable Git branch integration?",
                    default=settings.pi_extension_git_branch
                )


def _configure_features(interactive: bool) -> None:
    """Configure additional features."""
    settings = get_settings()
    
    console.print(Panel("[bold blue]Feature Configuration[/]", border_style="blue"))
    
    if interactive:
        settings.dreaming_enabled = Confirm.ask(
            "Enable Dreaming (background synthesis)?",
            default=settings.dreaming_enabled
        )
        settings.telemetry_enabled = Confirm.ask(
            "Enable telemetry/metrics?",
            default=settings.telemetry_enabled
        )
        
        if settings.telemetry_enabled:
            console.print("[yellow]⚠ Telemetry sends anonymous usage data[/]")


def _install_pi_extension() -> None:
    """Install Pi extension."""
    pi_manager = PiExtensionManager()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Installing Pi extension...", total=None)
        
        try:
            pi_manager.install()
            progress.update(task, completed=True)
            console.print("[green]✓ Pi extension installed[/]")
        except Exception as e:
            progress.update(task, completed=True)
            console.print(f"[yellow]⚠ Pi extension install failed: {e}[/]")


def _generate_systemd_services() -> None:
    """Generate systemd service files."""
    settings = get_settings()
    service_manager = ServiceManager()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Generating systemd services...", total=None)
        
        try:
            service_manager.generate_services()
            progress.update(task, completed=True)
            console.print("[green]✓ Systemd services generated[/]")
        except Exception as e:
            progress.update(task, completed=True)
            console.print(f"[yellow]⚠ Service generation failed: {e}[/]")


def _test_database_connection(url: str) -> bool:
    """Test database connection."""
    import subprocess
    
    # Try to run psql command
    try:
        result = subprocess.run(
            ["psql", url, "-c", "SELECT 1;"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        # psql not available or connection failed
        return False


def _is_port_available(port: int) -> bool:
    """Check if port is available."""
    import socket
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', port))
            return True
    except socket.error:
        return False


def run_diagnostics(fix: bool = False) -> list[str]:
    """Run diagnostic checks.
    
    Returns:
        List of issues found (empty if all checks passed)
    """
    issues = []
    settings = get_settings()
    
    console.print("[dim]Running diagnostics...[/]\n")
    
    # Check 1: Python version
    import sys
    if sys.version_info < (3, 10):
        issues.append(f"Python {sys.version_info.major}.{sys.version_info.minor} (require 3.10+)")
    else:
        console.print("[green]✓[/] Python version")
    
    # Check 2: Directories
    try:
        settings.ensure_directories()
        console.print("[green]✓[/] Directories accessible")
    except Exception as e:
        issues.append(f"Directory creation failed: {e}")
    
    # Check 3: Database
    if settings.database_url:
        try:
            if _test_database_connection(settings.get_database_url()):
                console.print("[green]✓[/] Database connection")
            else:
                issues.append("Database connection failed")
        except Exception as e:
            issues.append(f"Database check error: {e}")
    else:
        console.print("[yellow]⚠[/] Database not configured")
    
    # Check 4: Systemd
    try:
        result = subprocess.run(
            ["systemctl", "--user", "--version"],
            capture_output=True,
            timeout=5
        )
        if result.returncode == 0:
            console.print("[green]✓[/] Systemd available")
        else:
            issues.append("Systemd user mode not available")
    except Exception:
        issues.append("Systemd not found")
    
    # Check 5: Pi extension
    pi_manager = PiExtensionManager()
    if pi_manager.is_pi_installed():
        if pi_manager.is_installed():
            console.print("[green]✓[/] Pi extension")
        else:
            console.print("[yellow]⚠[/] Pi available but extension not installed")
            if fix:
                try:
                    pi_manager.install()
                    console.print("[green]✓[/] Pi extension installed")
                except Exception as e:
                    issues.append(f"Pi extension install failed: {e}")
    else:
        console.print("[dim]ℹ Pi not installed (optional)[/]")
    
    return issues