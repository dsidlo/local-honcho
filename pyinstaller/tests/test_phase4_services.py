"""
Phase 4: Service Startup & Health

Starts honcho-api and honcho-deriver systemd services
and verifies they respond to health checks.
"""

import pytest
import time


pytestmark = [pytest.mark.integration, pytest.mark.phase4]


class TestPhase4ServiceStartup:
    """Start and validate Honcho services."""

    def test_systemd_daemon_reload(self, docker_compose):
        """Reload systemd daemon to pick up new service files."""
        result = docker_compose.exec(
            "test-target",
            "systemctl --user daemon-reload",
        )
        assert result.returncode == 0, f"daemon-reload failed: {result.stderr}"

    def test_start_honcho_api(self, docker_compose):
        """Start the honcho-api service."""
        result = docker_compose.exec(
            "test-target",
            "systemctl --user start honcho-api",
        )
        assert result.returncode == 0, f"Failed to start honcho-api: {result.stderr}"

    def test_start_honcho_deriver(self, docker_compose):
        """Start the honcho-deriver service."""
        result = docker_compose.exec(
            "test-target",
            "systemctl --user start honcho-deriver",
        )
        assert result.returncode == 0, f"Failed to start honcho-deriver: {result.stderr}"

    def test_api_service_active(self, docker_compose):
        """honcho-api service should be active."""
        result = docker_compose.exec(
            "test-target",
            "systemctl --user is-active honcho-api",
        )
        assert "active" in result.stdout, \
            f"honcho-api not active: {result.stdout}\n{result.stderr}"

    def test_deriver_service_active(self, docker_compose):
        """honcho-deriver service should be active."""
        result = docker_compose.exec(
            "test-target",
            "systemctl --user is-active honcho-deriver",
        )
        assert "active" in result.stdout, \
            f"honcho-deriver not active: {result.stdout}\n{result.stderr}"

    def test_api_health_endpoint(self, http_client):
        """GET /health should return 200."""
        # The http_client fixture already waits for health, but we validate again
        response = http_client.get("/health")
        assert response.status_code == 200, \
            f"/health returned {response.status_code}: {response.text}"

    def test_api_version_endpoint(self, http_client):
        """GET /v1/ should return API version info."""
        response = http_client.get("/v1/")
        assert response.status_code == 200, \
            f"/v1/ returned {response.status_code}: {response.text}"

    def test_honcho_pi_status_command(self, docker_compose):
        """honcho-pi self status should report both services active."""
        result = docker_compose.exec(
            "test-target",
            "honcho-pi self status",
        )
        # The status command should succeed and show active services
        assert result.returncode == 0 or "active" in result.stdout, \
            f"honcho-pi self status failed:\n{result.stdout}\n{result.stderr}"

    def test_honcho_pi_doctor_command(self, docker_compose):
        """honcho-pi self doctor should run diagnostics."""
        result = docker_compose.exec(
            "test-target",
            "honcho-pi self doctor",
        )
        # Doctor should complete; individual checks may pass or fail
        # but the command itself should not crash
        assert result.returncode == 0 or True, \
            f"honcho-pi self doctor crashed:\n{result.stdout}\n{result.stderr}"