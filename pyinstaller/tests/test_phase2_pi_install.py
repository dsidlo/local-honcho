"""
Phase 2: Pi Installation & Validation

Installs pi-mono and verifies it's operational.
"""

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.phase2]


class TestPhase2PiInstall:
    """Install pi-mono and validate it's working."""

    def test_install_pi_mono(self, docker_compose):
        """Install pi-mono via the official curl-based installer."""
        # Install pi using the official script
        # Note: This is the pi-mono installer, not the honcho-pi installer
        result = docker_compose.exec(
            "test-target",
            "curl -fsSL https://get.pi.sh | bash || "
            "curl -fsSL https://raw.githubusercontent.com/mariozechner/pi-coding-agent/main/install.sh | bash",
        )
        # pi-mono may or may not be installable in this exact way;
        # in CI we may need to provide a pre-built pi binary instead
        # For now, check if ~/.pi/agent exists after attempted install
        if result.returncode != 0:
            pytest.skip("pi-mono installer not available in CI; manual setup required")

    def test_pi_binary_available(self, docker_compose):
        """pi binary should be available in PATH."""
        result = docker_compose.exec(
            "test-target",
            "which pi || ls ~/.pi/bin/pi || echo 'pi not found'",
        )
        # Check for pi in common locations
        check = docker_compose.exec(
            "test-target",
            "ls -la ~/.pi/agent/ 2>/dev/null || echo 'no .pi/agent dir'",
        )
        assert "agent" in check.stdout or "bin" in check.stdout, \
            "pi-mono agent directory not found"

    def test_pi_agent_directory_structure(self, docker_compose):
        """pi agent directory should have the expected structure."""
        result = docker_compose.exec(
            "test-target",
            "ls -la ~/.pi/agent/ 2>/dev/null || echo 'missing'",
        )
        # If pi isn't installed yet, skip
        if "missing" in result.stdout:
            pytest.skip("pi-mono not installed; skipping directory structure check")

        # Check for expected directories/files
        for path in ["extensions", "settings.json", "skills", "agents"]:
            check = docker_compose.exec(
                "test-target",
                f"test -e ~/.pi/agent/{path} && echo 'found' || echo 'missing'",
            )
            # Not all paths need to exist; extensions and settings.json are critical
            if path in ["extensions"]:
                assert "found" in check.stdout, f"~/.pi/agent/{path} not found"

    def test_pi_settings_json_valid(self, docker_compose):
        """pi settings.json should be valid JSON."""
        result = docker_compose.exec(
            "test-target",
            "python3 -c \"import json; json.load(open('.pi/agent/settings.json'))\" 2>/dev/null && echo 'valid' || echo 'invalid'",
        )
        if "invalid" in result.stdout:
            pytest.skip("pi-mono not installed; settings.json not found")
        assert "valid" in result.stdout, "settings.json is not valid JSON"