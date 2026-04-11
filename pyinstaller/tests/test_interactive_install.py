"""
Interactive Install Test

Tests the interactive (tmux-driven) installation flow of honcho-pi,
simulating a human walking through the configuration wizard step by step.
"""

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestInteractiveInstall:
    """Test the interactive install wizard using tmux + pexpect."""

    def test_full_interactive_install(self, docker_compose, tmux_driver):
        """Walk through complete interactive installation."""
        # Ensure we start fresh
        docker_compose.exec(
            "test-target",
            "rm -rf ~/.config/honcho-pi ~/.pi/agent/extensions/honcho.ts",
        )

        # Start the install command in tmux
        tmux_driver.send_keys("./honcho-pi install", enter=True)

        # Phase: Database Configuration
        assert tmux_driver.wait_for(r"Use Docker Postgres|Docker Postgres", timeout=15), \
            f"Missing Docker Postgres prompt. Panel:\n{tmux_driver.capture_pane()}"
        tmux_driver.send_keys("Y", enter=True)

        # Phase: Database password
        assert tmux_driver.wait_for(r"[Pp]ostgres password|[Pp]assword", timeout=10), \
            f"Missing password prompt. Panel:\n{tmux_driver.capture_pane()}"
        tmux_driver.send_keys("honcho_test_password", enter=True)

        # Phase: LLM Provider
        assert tmux_driver.wait_for(r"LLM [Pp]rovider|provider", timeout=10), \
            f"Missing LLM provider prompt. Panel:\n{tmux_driver.capture_pane()}"
        tmux_driver.send_keys("vllm", enter=True)

        # Phase: Embedding
        assert tmux_driver.wait_for(r"[Ee]mbedding|[Oo]llama.*embedding", timeout=10), \
            f"Missing embedding prompt. Panel:\n{tmux_driver.capture_pane()}"
        tmux_driver.send_keys("Y", enter=True)

        # Phase: Embedding model
        if tmux_driver.wait_for(r"[Mm]odel|nomic", timeout=5):
            tmux_driver.send_keys("bge-m3", enter=True)
        else:
            # May have accepted default
            tmux_driver.send_keys("", enter=True)

        # Phase: Reranker
        if tmux_driver.wait_for(r"[Rr]eranker", timeout=5):
            tmux_driver.send_keys("N", enter=True)

        # Phase: API port
        if tmux_driver.wait_for(r"[Aa]PI.*port|[Pp]ort.*8000", timeout=5):
            tmux_driver.send_keys("", enter=True)  # Accept default 8000

        # Phase: Pi extension
        assert tmux_driver.wait_for(r"[Pp]i.*extension|[Pp]i.*integration", timeout=10), \
            f"Missing Pi extension prompt. Panel:\n{tmux_driver.capture_pane()}"

        # Check if pi is installed first
        result = docker_compose.exec(
            "test-target",
            "test -d ~/.pi/agent && echo 'pi-found' || echo 'pi-missing'",
        )

        if "pi-found" in result.stdout:
            # Pi is installed, say yes to extension
            tmux_driver.send_keys("Y", enter=True)
        else:
            # Pi not installed, skip extension
            tmux_driver.send_keys("N", enter=True)

        # Phase: Dreamer
        if tmux_driver.wait_for(r"[Dd]reamer|[Dd]reaming", timeout=5):
            tmux_driver.send_keys("Y", enter=True)

        # Phase: Telemetry
        if tmux_driver.wait_for(r"[Tt]elemetry|[Mm]etrics", timeout=5):
            tmux_driver.send_keys("N", enter=True)

        # Wait for completion
        assert tmux_driver.wait_for(r"✓|[Cc]onfiguration saved|[Cc]onfigure", timeout=30), \
            f"Installation did not complete. Panel:\n{tmux_driver.capture_pane()}"

        # Verify .env was written
        result = docker_compose.exec(
            "test-target",
            "test -f ~/.config/honcho-pi/.env && echo 'found' || echo 'missing'",
        )
        assert "found" in result.stdout, ".env not created after interactive install"

    def test_interactive_install_reconfigure(self, docker_compose, tmux_driver):
        """Running install again should prompt for reconfiguration."""
        # Start configure command
        tmux_driver.send_keys("./honcho-pi self configure", enter=True)

        # Should see a reconfiguration prompt
        assert tmux_driver.wait_for(
            r"[Rr]econfigure|[Aa]lready configured|Database",
            timeout=15,
        ), f"Reconfigure prompt not seen. Panel:\n{tmux_driver.capture_pane()}"

    def test_interactive_doctor_command(self, docker_compose, tmux_driver):
        """The doctor command should run diagnostics interactively."""
        tmux_driver.send_keys("./honcho-pi self doctor", enter=True)

        # Should see diagnostic output
        assert tmux_driver.wait_for(
            r"[Dd]iagnostic|[Cc]heck|✓|✗",
            timeout=15,
        ), f"Doctor output not seen. Panel:\n{tmux_driver.capture_pane()}"