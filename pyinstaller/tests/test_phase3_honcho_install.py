"""
Phase 3: Honcho-Pi Installation

Installs honcho-pi via the self-contained binary using
both non-interactive and interactive (tmux) modes.
"""

import os
import pytest


pytestmark = [pytest.mark.integration, pytest.mark.phase3]


class TestPhase3HonchoInstallNonInteractive:
    """Test honcho-pi install in non-interactive mode."""

    def test_honcho_pi_install_non_interactive(self, docker_compose):
        """Run honcho-pi install --non-interactive and verify it completes."""
        # Set up environment for non-interactive install
        result = docker_compose.exec(
            "test-target",
            " ".join([
                "DATABASE_URL='postgresql+psycopg://postgres:honcho_test_password@postgres:5432/honcho'",
                "LLM_PROVIDER=vllm",
                "LLM_VLLM_BASE_URL=http://ollama:11434/v1",
                "LLM_VLLM_API_KEY=ollama",
                "LLM_EMBEDDING_PROVIDER=ollama",
                "LLM_OLLAMA_EMBEDDING_MODEL=bge-m3",
                "API_PORT=8000",
                "honcho-pi install --non-interactive",
            ]),
        )
        assert result.returncode == 0, (
            f"honcho-pi install failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_env_file_created(self, docker_compose):
        """Configuration .env file should exist after install."""
        result = docker_compose.exec(
            "test-target",
            "test -f ~/.config/honcho-pi/.env && echo 'found' || echo 'missing'",
        )
        assert "found" in result.stdout, "~/.config/honcho-pi/.env not created"

    def test_env_file_contains_required_keys(self, docker_compose):
        """The .env file should contain required configuration keys."""
        result = docker_compose.exec(
            "test-target",
            "cat ~/.config/honcho-pi/.env",
        )
        content = result.stdout

        required_keys = [
            "DATABASE_URL",
            "LLM_PROVIDER",
            "EMBEDDING_PROVIDER",
            "API_PORT",
        ]
        for key in required_keys:
            assert key in content, f"Missing required key '{key}' in .env file"

    def test_env_file_database_url_valid(self, docker_compose):
        """DATABASE_URL should be a valid PostgreSQL connection string."""
        result = docker_compose.exec(
            "test-target",
            "grep DATABASE_URL ~/.config/honcho-pi/.env",
        )
        assert "postgresql+psycopg://" in result.stdout, \
            f"DATABASE_URL not a valid PostgreSQL URL: {result.stdout}"

    def test_systemd_services_generated(self, docker_compose):
        """systemd service files should be generated."""
        for service in ["honcho-api", "honcho-deriver"]:
            result = docker_compose.exec(
                "test-target",
                f"test -f ~/.config/systemd/user/{service}.service && echo 'found' || echo 'missing'",
            )
            assert "found" in result.stdout, \
                f"~/.config/systemd/user/{service}.service not generated"

    def test_systemd_service_content(self, docker_compose):
        """systemd service files should contain required directives."""
        for service in ["honcho-api", "honcho-deriver"]:
            result = docker_compose.exec(
                "test-target",
                f"cat ~/.config/systemd/user/{service}.service",
            )
            content = result.stdout
            # Check for essential systemd directives
            assert "[Unit]" in content, f"{service}.service missing [Unit] section"
            assert "[Service]" in content, f"{service}.service missing [Service] section"
            assert "ExecStart" in content, f"{service}.service missing ExecStart"


class TestPhase3HonchoInstallInteractive:
    """Test honcho-pi install in interactive mode using tmux."""

    def test_interactive_install_wizard(self, docker_compose, tmux_driver):
        """Walk through the interactive install wizard via tmux."""
        # Start the install command
        tmux_driver.send_keys("./honcho-pi install", enter=True)

        # Wait for the first prompt
        assert tmux_driver.wait_for(r"Use Docker Postgres", timeout=15), \
            "Did not see 'Use Docker Postgres' prompt"

        # Answer: Yes, use Docker Postgres
        tmux_driver.send_keys("Y", enter=True)

        # Wait for Postgres password prompt
        assert tmux_driver.wait_for(r"[Pp]ostgres password", timeout=10), \
            "Did not see Postgres password prompt"

        # Enter test password
        tmux_driver.send_keys("honcho_test_password", enter=True)

        # Wait for LLM provider prompt
        assert tmux_driver.wait_for(r"LLM [Pp]rovider", timeout=10), \
            "Did not see LLM provider prompt"

        # Choose vllm
        tmux_driver.send_keys("vllm", enter=True)

        # Wait for embedding prompt
        assert tmux_driver.wait_for(r"[Ee]mbedding|[Oo]llama", timeout=10), \
            "Did not see embedding configuration prompt"

        # Choose Ollama
        tmux_driver.send_keys("Y", enter=True)

        # Wait for bge-m3 model prompt
        assert tmux_driver.wait_for(r"[Ee]mbedding model|[Oo]llama model", timeout=10), \
            "Did not see embedding model prompt"

        # Accept default or type bge-m3
        tmux_driver.send_keys("bge-m3", enter=True)

        # Continue through remaining prompts with defaults
        # Wait for API port prompt
        assert tmux_driver.wait_for(r"[Aa]PI port|[Pp]ort", timeout=10), \
            "Did not see API port prompt"
        tmux_driver.send_keys("", enter=True)  # Accept default

        # Wait for Dreamer prompt
        assert tmux_driver.wait_for(r"[Dd]reamer|[Dd]reaming", timeout=10), \
            "Did not see Dreamer prompt"
        tmux_driver.send_keys("Y", enter=True)

        # Wait for Pi extension prompt
        assert tmux_driver.wait_for(r"[Pp]i extension|[Pp]i integration", timeout=10), \
            "Did not see Pi extension prompt"
        tmux_driver.send_keys("N", enter=True)  # Skip extension in interactive test

        # Wait for configuration to complete
        assert tmux_driver.wait_for(r"✓|[Cc]onfiguration saved|[Cc]onfigured", timeout=30), \
            f"Configuration did not complete. Panel output:\n{tmux_driver.capture_pane()}"

    def test_interactive_env_file_valid(self, docker_compose):
        """After interactive install, verify env file."""
        result = docker_compose.exec(
            "test-target",
            "test -f ~/.config/honcho-pi/.env && echo 'found' || echo 'missing'",
        )
        assert "found" in result.stdout, ".env file not created by interactive install"