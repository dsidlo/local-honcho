"""PyApp self-management commands for Honcho Pi.

These commands extend PyApp's built-in `self` functionality with
Honcho-specific operations like status checks, configuration,
and service management.
"""

from honcho_pi.commands.status import status
from honcho_pi.commands.configure import configure
from honcho_pi.commands.doctor import doctor
from honcho_pi.commands.self_cmd import self_group

__all__ = ["status", "configure", "doctor", "self_group"]
