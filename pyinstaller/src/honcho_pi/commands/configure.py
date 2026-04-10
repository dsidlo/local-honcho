"""Self configure command for Honcho Pi.

Re-runs the interactive configuration wizard to update settings,
services, and integration points.
"""

import os
import click
from pathlib import Path


def prompt_database():
    """Interactive database configuration."""
    click.echo()
    click.echo(click.style("Database Configuration", fg="green", bold=True))
    
    # Docker vs external
    use_docker = click.confirm("Use Docker Postgres with pgvector?", default=True)
    
    if use_docker:
        return "docker", {
            "image": "ankane/pgvector:latest",
            "container_name": "honcho-postgres",
            "port": "5432",
            "database": "honcho",
            "user": "postgres",
            "password": click.prompt("Postgres password", default="honcho_default", hide_input=True),
        }
    else:
        db_url = click.prompt(
            "PostgreSQL connection URL",
            default="postgresql+psycopg://user:pass@localhost:5432/honcho"
        )
        return "external", {"url": db_url}


def prompt_llm():
    """Interactive LLM configuration."""
    click.echo()
    click.echo(click.style("LLM Configuration", fg="green", bold=True))
    
    provider = click.prompt(
        "LLM Provider",
        type=click.Choice(["anthropic", "openai", "groq", "gemini", "vllm"], case_sensitive=False),
        default="anthropic"
    ).lower()
    
    api_key = click.prompt(f"{provider.title()} API key", hide_input=True)
    
    return {
        "provider": provider,
        "api_key": api_key,
        "model": click.prompt("Model name", default="claude-3-sonnet-20240229" if provider == "anthropic" else "gpt-4"),
    }


def prompt_embedding():
    """Interactive embedding configuration."""
    click.echo()
    click.echo(click.style("Embedding Configuration", fg="green", bold=True))
    
    provider = click.prompt(
        "Embedding provider",
        type=click.Choice(["openai", "ollama"], case_sensitive=False),
        default="openai"
    ).lower()
    
    if provider == "openai":
        return {
            "provider": "openai",
            "model": "text-embedding-ada-002",
        }
    else:
        return {
            "provider": "ollama",
            "model": "nomic-embed-text:latest",
            "base_url": "http://localhost:11434",
        }


def prompt_pi_integration():
    """Interactive Pi extension configuration."""
    click.echo()
    click.echo(click.style("Pi Integration", fg="green", bold=True))
    
    install_ext = click.confirm("Install Pi extension?", default=True)
    if not install_ext:
        return None
    
    return {
        "install": True,
        "enable_hooks": click.confirm("Enable observation hooks?", default=True),
        "enable_git": click.confirm("Enable Git branch integration?", default=True),
    }


def prompt_services():
    """Interactive service configuration."""
    click.echo()
    click.echo(click.style("Services", fg="green", bold=True))
    
    return {
        "api_port": click.prompt("API port", default=8000, type=int),
        "enable_dreamer": click.confirm("Enable Dreamer (background synthesis)?", default=True),
        "enable_telemetry": click.confirm("Enable telemetry/metrics?", default=False),
    }


def write_config(config, config_dir):
    """Write configuration files."""
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    
    env_file = config_dir / ".env"
    
    # Build .env content
    lines = [
        f"# Honcho Pi Configuration - Generated",
        "",
        "# Database",
    ]
    
    if config["database"][0] == "docker":
        db_config = config["database"][1]
        db_url = f"postgresql+psycopg://{db_config['user']}:{db_config['password']}@localhost:{db_config['port']}/{db_config['database']}"
        lines.append(f"DATABASE_URL={db_url}")
        lines.append(f"DB_USE_DOCKER=true")
    else:
        lines.append(f"DATABASE_URL={config['database'][1]['url']}")
    
    lines.extend([
        "",
        "# LLM Configuration",
        f"LLM_PROVIDER={config['llm']['provider']}",
        f"LLM_{config['llm']['provider'].upper()}_API_KEY={config['llm']['api_key']}",
        f"LLM_MODEL={config['llm']['model']}",
        "",
        "# Embedding Configuration",
        f"LLM_EMBEDDING_PROVIDER={config['embedding']['provider']}",
    ])
    
    if config['embedding']['provider'] == 'ollama':
        lines.append(f"LLM_OLLAMA_BASE_URL={config['embedding']['base_url']}")
        lines.append(f"LLM_OLLAMA_EMBEDDING_MODEL={config['embedding']['model']}")
    else:
        lines.append(f"LLM_OPENAI_EMBEDDING_MODEL={config['embedding']['model']}")
    
    lines.extend([
        "",
        "# Services",
        f"HONCHO_BASE_URL=http://localhost:{config['services']['api_port']}",
        f"API_PORT={config['services']['api_port']}",
        f"DREAMING_ENABLED={str(config['services']['enable_dreamer']).lower()}",
        f"TELEMETRY_ENABLED={str(config['services']['enable_telemetry']).lower()}",
    ])
    
    if config.get('pi'):
        lines.extend([
            "",
            "# Pi Integration",
            f"PI_EXTENSION_ENABLED=true",
            f"PI_OBSERVATION_HOOKS={str(config['pi']['enable_hooks']).lower()}",
            f"PI_GIT_INTEGRATION={str(config['pi']['enable_git']).lower()}",
        ])
    
    # Write file
    env_file.write_text("\n".join(lines) + "\n")
    os.chmod(env_file, 0o600)  # Restrict permissions
    
    return env_file


@click.command(name="configure")
@click.option('--non-interactive', is_flag=True, help='Use defaults without prompts')
@click.option('--config-dir', default="~/.config/honcho-pi", help='Configuration directory')
def configure(non_interactive, config_dir):
    """Re-run configuration wizard.
    
    Interactive setup for database, LLM, embedding, Pi integration,
    and service configuration. Updates .env and systemd services.
    """
    config_dir = Path(config_dir).expanduser()
    
    click.echo(click.style("Honcho Pi Configuration", fg="green", bold=True))
    click.echo("=" * 40)
    
    if non_interactive:
        # Use sensible defaults
        config = {
            "database": ("docker", {"port": "5432", "database": "honcho", "user": "postgres", "password": "honcho_default"}),
            "llm": {"provider": "anthropic", "api_key": "", "model": "claude-3-sonnet-20240229"},
            "embedding": {"provider": "openai", "model": "text-embedding-ada-002"},
            "services": {"api_port": 8000, "enable_dreamer": True, "enable_telemetry": False},
            "pi": None,
        }
        click.echo("Using default configuration...")
    else:
        # Interactive prompts
        config = {
            "database": prompt_database(),
            "llm": prompt_llm(),
            "embedding": prompt_embedding(),
            "services": prompt_services(),
            "pi": prompt_pi_integration(),
        }
    
    # Write configuration
    click.echo()
    click.echo("Writing configuration...")
    env_file = write_config(config, config_dir)
    
    click.echo(click.style(f"✓ Configuration written to {env_file}", fg="green"))
    
    # Install Pi extension if requested
    if config.get("pi") and config["pi"].get("install"):
        click.echo()
        click.echo("Installing Pi extension...")
        # Call pi_integration module
        from honcho_pi import pi_integration
        pi_integration.install_extension(config["pi"])
    
    # Restart services if they exist
    click.echo()
    click.echo("To apply changes, restart services:")
    click.echo("  honcho-pi self restart")
    click.echo("Or individually:")
    click.echo("  systemctl --user restart honcho-api honcho-deriver")
