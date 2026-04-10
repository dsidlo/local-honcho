"""Self-management command group for PyApp.

Provides the base `self` group that contains all management commands.
PyApp reserves this namespace - custom commands extend it.
"""

import click
from honcho_pi.commands.status import status
from honcho_pi.commands.configure import configure
from honcho_pi.commands.doctor import doctor


@click.group()
@click.pass_context
def self_group(ctx):
    """Self-management commands.
    
    Manage the Honcho Pi installation: check status, update,
    reconfigure, and diagnose issues.
    
    \b
    Common commands:
        honcho-pi self status       Show service status
        honcho-pi self configure     Re-run configuration
        honcho-pi self doctor        Diagnose issues
        honcho-pi self update        Check for updates
    
    Note: update is handled by PyApp's built-in self command.
    """
    pass


# Add subcommands
self_group.add_command(status)
self_group.add_command(configure)
self_group.add_command(doctor)
# self.update is provided by PyApp


# Aliases for convenience
self_group.add_command(status, name="stat")
self_group.add_command(configure, name="config")
self_group.add_command(doctor, name="diag")
