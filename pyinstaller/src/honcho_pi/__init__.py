"""Honcho Pi - PyApp-native distribution of Honcho memory service.

This package provides a CLI and management interface for the Honcho
memory service with pi-mono integration, distributed via PyApp.

Example:
    $ honcho-pi --help
    $ honcho-pi self install
    $ honcho-pi self status
"""

__version__ = "1.0.0"
__all__ = ["cli", "bootstrap", "config", "services", "pi_integration"]
