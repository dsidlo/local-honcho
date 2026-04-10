"""First-run configuration and bootstrap logic for Honcho Pi."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt, PromptError
from rich.status import Status

from honcho_pi.config import Settings

console = Console()


class BootstrapError(Exception):
    """Bootstrap error."""
    pass


def run_configuration(interactive: bool = True, settings: Optional[Settings] = None) -> Settings:
    """Run the configuration wizard.
    
    Args:
        interactive: Whether to prompt user for input
        settings: Existing settings to modify, or None for fresh config
        
    Returns:
        Configured Settings instance
    """
    if settings is None:
        settings = Settings.from_env()
    
    console.print(Panel.fit(
        "[bold green]🎯 Honcho Pi Setup[/bold green]",
        border_style="green"
    ))
    
    # Ensure directories exist
    settings.ensure_directories()
    
    # Database Configuration
    if interactive:
        _configure_database_interactive(settings)
    else:
        _configure_database_auto(settings)
    
    # LLM Configuration
    if interactive:
        _configure_llm_interactive(settings)
    else:
        _configure_llm_auto(settings)
    
    # Embedding & Reranker
    if interactive:
        _configure_embeddings_interactive(settings)
        _configure_reranker_interactive(settings)
    
    # Pi Extension
    if interactive:
        _configure_pi_extension_interactive(settings)
    
    # Service settings
    if interactive:
        _configure_service_interactive(settings)
    
    # Save configuration
    _save_configuration(settings)
    
    console.print("\n[bold green]✓ Configuration complete![/bold green]")
    console.print(f"Settings saved to: {settings.env_file}")
    
    return settings


def _configure_database_interactive(settings: Settings):
    """Configure database interactively."""
    console.print("\n[bold]Database Configuration[/bold]")
    
    use_docker = Confirm.ask(
        "Use Docker Postgres with pgvector?",
        default=True
    )
    settings.use_docker_db = use_docker
    
    if use_docker:
        # Check Docker is available
        if not _command_exists("docker"):
            console.print("[yellow]⚠ Docker not found. Please install Docker first.[/yellow]")
            console.print("Run: curl -fsSL https://get.docker.com | sh")
            if not Confirm.ask("Continue anyway?"):
                raise BootstrapError("Docker required for automatic database setup")
        
        settings.database_url = (
            "postgresql+psycopg://postgres:honcho_password@localhost:5432/honcho"
        )
        
        # Start Docker container
        if Confirm.ask("Start Docker Postgres now?", default=True):
            with Status("Starting PostgreSQL with pgvector...", console=console):
                _start_docker_postgres()
    else:
        # Manual URI input
        db_uri = Prompt.ask(
            "Enter Postgres URI",
            default="postgresql+psycopg://user:pass@localhost:5432/honcho"
        )
        settings.database_url = db_uri
        
        # Test connection
        if Confirm.ask("Test database connection?", default=True):
            with Status("Testing database connection...", console=console):
                if _test_database_connection(db_uri):
                    console.print("[green]✓ Database connection successful[/green]")
                else:
                    console.print("[yellow]⚠ Database connection failed[/yellow]")
                    msg = "Please ensure pgvector extension is installed and database is accessible"
                    console.print(f"[dim]{msg}[/dim]")


def _configure_database_auto(settings: Settings):
    """Configure database automatically (non-interactive)."""
    if settings.database_url:
        return
    
    # Try Docker first
    if _command_exists("docker"):
        settings.use_docker_db = True
        settings.database_url = (
            "postgresql+psycopg://postgres:honcho_password@localhost:5432/honcho"
        )
    else:
        # Use default local PostgreSQL
        settings.database_url = (
            "postgresql+psycopg://postgres:postgres@localhost:5432/honcho"
        )


def _configure_llm_interactive(settings: Settings):
    """Configure LLM provider interactively."""
    console.print("\n[bold]LLM Configuration[/bold]")
    
    provider = Prompt.ask(
        "LLM Provider",
        choices=["anthropic", "openai", "groq", "gemini"],
        default=settings.llm_provider
    )
    settings.llm_provider = provider
    
    # Check for existing key in environment
    env_key_name = f"LLM_{provider.upper()}_API_KEY"
    existing_key = os.getenv(env_key_name)
    
    if existing_key:
        masked = existing_key[:8] + "..." + existing_key[-4:]
        console.print(f"[dim]Using existing API key: {masked}[/dim]")
    else:
        key = console.input(f"Enter your {provider.capitalize()} API key (hidden): ")
        if key:
            settings.llm_api_key = key
            # Don't save directly - will be referenced in env file
        else:
            console.print("[yellow]⚠ No API key provided. Some features may not work.[/yellow]")


def _configure_llm_auto(settings: Settings):
    """Configure LLM automatically (non-interactive)."""
    # Check for existing keys
    for provider in ["anthropic", "openai", "groq", "gemini"]:
        key_name = f"LLM_{provider.upper()}_API_KEY"
        if os.getenv(key_name):
            settings.llm_provider = provider
            settings.llm_api_key = os.getenv(key_name)
            break


def _configure_embeddings_interactive(settings: Settings):
    """Configure embedding provider interactively."""
    console.print("\n[bold]Embedding Configuration[/bold]")
    
    provider = Prompt.ask(
        "Embedding provider",
        choices=["openai", "ollama"],
        default=settings.embedding_provider
    )
    settings.embedding_provider = provider
    
    if provider == "ollama":
        settings.ollama_url = Prompt.ask(
            "Ollama URL",
            default="http://localhost:11434"
        )
        
        # Pull embedding model
        if Confirm.ask("Pull nomic-embed-text model?", default=True):
            with Status("Pulling embedding model...", console=console):
                _run_ollama_pull("nomic-embed-text")
    else:
        settings.embedding_model = "text-embedding-3-small"


def _configure_reranker_interactive(settings: Settings):
    """Configure reranker interactively."""
    console.print("\n[bold]Reranker Configuration[/bold]")
    
    enabled = Confirm.ask(
        "Enable reranker for better search results?",
        default=False
    )
    settings.reranker_enabled = enabled
    
    if enabled and settings.embedding_provider == "ollama":
        if Confirm.ask("Pull bge-reranker-large model?", default=True):
            with Status("Pulling reranker model...", console=console):
                _run_ollama_pull("qllama/bge-reranker-large:f16")


def _configure_pi_extension_interactive(settings: Settings):
    """Configure Pi extension interactively."""
    console.print("\n[bold]Pi Extension Configuration[/bold]")
    
    # Check if Pi is installed
    pi_dir = settings.pi_agent_dir
    if not pi_dir.exists():
        console.print(f"[yellow]⚠ Pi not found at {pi_dir}[/yellow]")
        console.print("Install Pi first: npm install -g pi-mono")
        return
    
    console.print(f"[green]✓ Pi found at {pi_dir}[/green]")
    
    install_extension = Confirm.ask(
        "Install Honcho extension for Pi?",
        default=True
    )
    
    if install_extension:
        from honcho_pi.pi_integration import install_pi_extension
        try:
            install_pi_extension(settings)
            console.print("[green]✓ Pi extension installed[/green]")
        except Exception as e:
            console.print(f"[red]✗ Failed to install Pi extension: {e}[/red]")


def _configure_service_interactive(settings: Settings):
    """Configure service settings interactively."""
    console.print("\n[bold]Service Configuration[/bold]")
    
    settings.api_port = IntPrompt.ask(
        "API Port",
        default=settings.api_port
    )
    
    settings.dreaming_enabled = Confirm.ask(
        "Enable background dreaming/synthesis?",
        default=settings.dreaming_enabled
    )


def _save_configuration(settings: Settings):
    """Save configuration to environment file."""
    try:
        settings.save_env_file()
    except Exception as e:
        raise BootstrapError(f"Failed to save configuration: {e}")


def _command_exists(cmd: str) -> bool:
    """Check if a command exists in PATH."""
    try:
        subprocess.run(
            ["which", cmd],
            capture_output=True,
            check=True
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _start_docker_postgres():
    """Start Docker PostgreSQL with pgvector."""
    cmd = [
        "docker", "run", "-d",
        "--name", "honcho-postgres",
        "-e", "POSTGRES_PASSWORD=honcho_password",
        "-e", "POSTGRES_DB=honcho",
        "-p", "5432:5432",
        "--restart", "unless-stopped",
        "ankane/pgvector:latest"
    ]
    
    try:
        # Check if already running
        result = subprocess.run(
            ["docker", "ps", "-q", "-f", "name=honcho-postgres"],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            console.print("[dim]Using existing PostgreSQL container[/dim]")
            return
        
        # Remove old container if exists
        subprocess.run(
            ["docker", "rm", "-f", "honcho-postgres"],
            capture_output=True
        )
        
        # Start new container
        subprocess.run(cmd, check=True, capture_output=True)
        
        # Wait for PostgreSQL to be ready
        import time
        time.sleep(3)
        
    except subprocess.CalledProcessError as e:
        raise BootstrapError(f"Failed to start Docker PostgreSQL: {e}")


def _test_database_connection(uri: str) -> bool:
    """Test database connection."""
    # Extract connection info from URI
    try:
        import psycopg
        conn = psycopg.connect(uri, connect_timeout=5)
        conn.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


def _run_ollama_pull(model: str):
    """Pull a model with Ollama."""
    try:
        subprocess.run(
            ["ollama", "pull", model],
            check=True,
            capture_output=True
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]⚠ Failed to pull {model}: {e}[/yellow]")


def check_prerequisites() -> list[str]:
    """Check system prerequisites and return list of issues."""
    issues = []
    
    # Check Python version
    if sys.version_info < (3, 10):
        issues.append(f"Python 3.10+ required, found {sys.version_info.major}.{sys.version_info.minor}")
    
    # Check essential commands
    for cmd in ["curl", "tar"]:
        if not _command_exists(cmd):
            issues.append(f"Missing command: {cmd}")
    
    # Check optional but recommended
    if not _command_exists("docker"):
        issues.append("Docker not found (optional, but recommended for database)")
    
    if not _command_exists("systemctl"):
        issues.append("systemd not found - services cannot be managed automatically")
    
    return issues