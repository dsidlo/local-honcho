"""Command-line interface for Honcho Pi.

Provides the main CLI and self-management commands for the Honcho Pi
distribution. This is the primary entry point for the PyApp binary.
"""

import os
import sys
import click
from pathlib import Path

from honcho_pi import __version__


# Detect PyApp environment
IS_PYAPP = os.environ.get("PYAPP") == "1"
PYAPP_COMMAND_NAME = os.environ.get("PYAPP_COMMAND_NAME", "honcho-pi")


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="honcho-pi")
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
@click.pass_context
def main(ctx, verbose):
    """Honcho Pi - Memory service for pi-mono.
    
    A PyApp-native distribution of the Honcho memory service
    with pi-mono integration.
    
    \b
    Quick commands:
        honcho-pi --help          Show this help
        honcho-pi self install      Run setup wizard
        honcho-pi self status       Check system status
        honcho-pi self configure     Re-run configuration
    
    For full documentation: https://docs.honcho.dev/
    """
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    
    if ctx.invoked_subcommand is None:
        # No subcommand - show help
        click.echo(ctx.get_help())


# Import and register subcommands
from honcho_pi.commands.self_cmd import self_group
from honcho_pi.services import start_services, stop_services, check_service_status
from honcho_pi.bootstrap import run_configuration

main.add_command(self_group)


@click.command()
@click.option('--force', '-f', is_flag=True, help='Force reconfiguration')
@click.option('--non-interactive', '-n', is_flag=True, help='Use defaults, no prompts')
def install(force, non_interactive):
    """Install and configure Honcho Pi.
    
    Runs the first-time configuration wizard to set up:
    - Database (Docker Postgres or external)
    - LLM provider and API keys
    - Embedding and reranker configuration
    - Pi extension integration
    - Systemd services
    """
    try:
        run_configuration(
            interactive=not non_interactive,
            force=force
        )
    except Exception as e:
        click.echo(click.style(f"Configuration failed: {e}", fg="red"))
        sys.exit(1)


main.add_command(install)


@click.command()
@click.option('--services', '-s', multiple=True, type=click.Choice(['api', 'deriver', 'all']), 
              default=['all'], help='Services to start')
def start(services):
    """Start Honcho services."""
    services = list(services)
    if 'all' in services:
        services = ['api', 'deriver']
    click.echo("Starting services...")
    try:
        start_services(services)
        click.echo(click.style("✓ Services started", fg="green"))
    except Exception as e:
        click.echo(click.style(f"✗ Failed to start: {e}", fg="red"))
        sys.exit(1)


@click.command()
@click.option('--services', '-s', multiple=True, type=click.Choice(['api', 'deriver', 'all']),
              default=['all'], help='Services to stop')
def stop(services):
    """Stop Honcho services."""
    services = list(services)
    if 'all' in services:
        services = ['api', 'deriver']
    click.echo("Stopping services...")
    try:
        stop_services(services)
        click.echo(click.style("✓ Services stopped", fg="green"))
    except Exception as e:
        click.echo(click.style(f"✗ Failed to stop: {e}", fg="red"))


@click.command()
def restart():
    """Restart all Honcho services."""
    click.echo("Restarting services...")
    try:
        stop_services()
        start_services()
        click.echo(click.style("✓ Services restarted", fg="green"))
    except Exception as e:
        click.echo(click.style(f"✗ Failed to restart: {e}", fg="red"))
        sys.exit(1)


@click.command()
@click.option('--follow', '-f', is_flag=True, help='Follow logs')
def logs(follow):
    """View service logs."""
    import subprocess
    cmd = ["journalctl", "--user", "-u", "honcho-api", "-u", "honcho-deriver"]
    if follow:
        cmd.append("-f")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


main.add_command(start)
main.add_command(stop)
main.add_command(restart)
main.add_command(logs)


def run():
    """Entry point for console script."""
    return main()


if __name__ == "__main__":
    sys.exit(run())
