"""
Phase 6: Pi Extension Validation

Verifies that the honcho.ts Pi extension is properly installed
and configured in the pi-mono agent directory.
"""

import json
import pytest


pytestmark = [pytest.mark.integration, pytest.mark.phase6]


class TestPhase6ExtensionValidation:
    """Validate the Pi extension was installed correctly."""

    def test_honcho_extension_file_exists(self, docker_compose):
        """honcho.ts should be installed in ~/.pi/agent/extensions/."""
        result = docker_compose.exec(
            "test-target",
            "test -f ~/.pi/agent/extensions/honcho.ts && echo 'found' || echo 'missing'",
        )
        assert "found" in result.stdout, \
            "honcho.ts not found in ~/.pi/agent/extensions/"

    def test_honcho_extension_not_empty(self, docker_compose):
        """honcho.ts should not be an empty file."""
        result = docker_compose.exec(
            "test-target",
            "wc -c ~/.pi/agent/extensions/honcho.ts 2>/dev/null || echo '0 missing'",
        )
        # The extension file should have meaningful content (>100 bytes)
        parts = result.stdout.strip().split()
        if len(parts) >= 1:
            size = int(parts[0]) if parts[0].isdigit() else 0
            assert size > 100, f"honcho.ts is too small ({size} bytes), likely empty/corrupt"

    def test_extension_contains_api_url(self, docker_compose):
        """honcho.ts should contain the HONCHO_BASE_URL configuration."""
        result = docker_compose.exec(
            "test-target",
            "grep -c 'HONCHO_BASE_URL\\|localhost:8000\\|honcho_api_url' ~/.pi/agent/extensions/honcho.ts",
        )
        # The extension should reference the API URL
        count = result.stdout.strip()
        assert count.isdigit() and int(count) > 0, \
            "honcho.ts does not contain API URL references"

    def test_extension_contains_register_tool(self, docker_compose):
        """honcho.ts should register honcho_store or honcho_chat tools."""
        result = docker_compose.exec(
            "test-target",
            "grep -c 'registerTool\\|pi.registerTool' ~/.pi/agent/extensions/honcho.ts",
        )
        count = result.stdout.strip()
        assert count.isdigit() and int(count) > 0, \
            "honcho.ts does not register any Pi tools"

    def test_extension_has_extension_handler(self, docker_compose):
        """honcho.ts should have an extension handler (export default function)."""
        result = docker_compose.exec(
            "test-target",
            "grep -c 'export default\\|export default function' ~/.pi/agent/extensions/honcho.ts",
        )
        count = result.stdout.strip()
        assert count.isdigit() and int(count) > 0, \
            "honcho.ts does not export a default extension handler"

    def test_pi_settings_json_exists(self, docker_compose):
        """~/.pi/agent/settings.json should exist."""
        result = docker_compose.exec(
            "test-target",
            "test -f ~/.pi/agent/settings.json && echo 'found' || echo 'missing'",
        )
        assert "found" in result.stdout, "settings.json not found"

    def test_pi_settings_json_valid(self, docker_compose):
        """settings.json should be valid JSON."""
        result = docker_compose.exec(
            "test-target",
            "python3 -c \"import json; json.load(open('.pi/agent/settings.json'))\" && echo 'valid' || echo 'invalid'",
        )
        assert "valid" in result.stdout, "settings.json is not valid JSON"

    def test_pi_settings_honcho_enabled(self, docker_compose):
        """settings.json should have honcho.enabled = true."""
        result = docker_compose.exec(
            "test-target",
            "python3 -c \"import json; data=json.load(open('.pi/agent/settings.json')); print(data.get('honcho',{}).get('enabled','not-set'))\"",
        )
        value = result.stdout.strip().lower()
        assert value == "true", f"honcho.enabled is '{value}', expected 'true'"

    def test_pi_settings_has_api_url(self, docker_compose):
        """settings.json should have honcho.apiUrl configured."""
        result = docker_compose.exec(
            "test-target",
            "python3 -c \"import json; data=json.load(open('.pi/agent/settings.json')); print(data.get('honcho',{}).get('apiUrl','not-set'))\"",
        )
        api_url = result.stdout.strip()
        assert api_url != "not-set" and api_url.startswith("http"), \
            f"honcho.apiUrl is '{api_url}', expected an HTTP URL"

    def test_extension_install_via_honcho_pi(self, docker_compose):
        """honcho-pi should be able to verify extension installation status."""
        result = docker_compose.exec(
            "test-target",
            "honcho-pi self status",
        )
        # The status command should report the extension as installed
        # (either in JSON or human-readable output)
        output = result.stdout + result.stderr
        # We don't mandate exact wording, but status should not error
        assert result.returncode == 0 or "extension" in output.lower(), \
            f"honcho-pi self status failed and doesn't mention extension:\n{output}"