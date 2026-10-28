"""Unit tests for honcho_pi.bootstrap."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from honcho_pi.bootstrap import (
    BootstrapError,
    _command_exists,
    _configure_database_auto,
    _configure_database_interactive,
    _configure_embeddings_interactive,
    _configure_llm_auto,
    _configure_llm_interactive,
    _configure_pi_extension_interactive,
    _configure_reranker_interactive,
    _configure_service_interactive,
    _run_ollama_pull,
    _save_configuration,
    _start_docker_postgres,
    _test_database_connection,
    check_prerequisites,
    run_configuration,
)
from honcho_pi.config import Settings


def make_settings(tmp_path) -> Settings:
    return Settings(
        config_dir=tmp_path / "config",
        honcho_install_dir=tmp_path / "install",
        llm_provider="anthropic",
        llm_api_key="k",
        database_url="postgresql://x",
    )


class TestRunConfiguration:
    def test_auto_mode(self, tmp_path):
        s = make_settings(tmp_path)
        s.database_url = "postgresql://localhost/honcho"
        with patch("honcho_pi.bootstrap.console") as mock_console, patch(
            "honcho_pi.bootstrap._save_configuration"
        ) as mock_save:
            result = run_configuration(interactive=False, settings=s)
        assert result is s
        mock_save.assert_called_once_with(s)

    def test_auto_mode_sets_defaults(self, tmp_path):
        s = make_settings(tmp_path)
        s.database_url = None
        with patch("honcho_pi.bootstrap.console") as mock_console, patch(
            "honcho_pi.bootstrap._save_configuration"
        ), patch("honcho_pi.bootstrap._command_exists", return_value=False):
            run_configuration(interactive=False, settings=s)
        assert s.database_url is not None


class TestDatabaseConfig:
    def test_auto_uses_existing_url(self, tmp_path):
        s = make_settings(tmp_path)
        s.database_url = "postgresql://mine"
        _configure_database_auto(s)
        assert s.database_url == "postgresql://mine"

    def test_auto_docker_when_available(self, tmp_path):
        s = make_settings(tmp_path)
        s.database_url = None
        with patch("honcho_pi.bootstrap._command_exists", return_value=True):
            _configure_database_auto(s)
        assert s.use_docker_db is True
        assert "honcho_password" in s.database_url

    def test_auto_no_docker_default(self, tmp_path):
        s = make_settings(tmp_path)
        s.database_url = None
        with patch("honcho_pi.bootstrap._command_exists", return_value=False):
            _configure_database_auto(s)
        assert s.use_docker_db is False
        assert "postgres:postgres" in s.database_url

    def test_interactive_docker_path(self, tmp_path):
        s = make_settings(tmp_path)
        with patch("honcho_pi.bootstrap.Confirm.ask", side_effect=[True, True, True]) as mock_ask, patch(
            "honcho_pi.bootstrap._start_docker_postgres"
        ) as mock_start, patch("honcho_pi.bootstrap._command_exists", return_value=True):
            _configure_database_interactive(s)
        assert s.use_docker_db is True
        mock_start.assert_called_once()

    def test_interactive_docker_missing_raises(self, tmp_path):
        s = make_settings(tmp_path)
        with patch("honcho_pi.bootstrap.Confirm.ask", side_effect=[True, False]) as mock_ask, patch(
            "honcho_pi.bootstrap._command_exists", return_value=False
        ):
            with pytest.raises(BootstrapError):
                _configure_database_interactive(s)

    def test_interactive_manual_uri(self, tmp_path):
        s = make_settings(tmp_path)
        with patch("honcho_pi.bootstrap.Confirm.ask", side_effect=[False, False]) as mock_ask, patch(
            "honcho_pi.bootstrap.Prompt.ask", return_value="postgresql://u:p@localhost:5432/db"
        ) as mock_prompt, patch("honcho_pi.bootstrap._test_database_connection", return_value=True):
            _configure_database_interactive(s)
        assert s.database_url == "postgresql://u:p@localhost:5432/db"


class TestLLMConfig:
    def test_auto_finds_key(self, tmp_path, monkeypatch):
        s = make_settings(tmp_path)
        monkeypatch.setenv("LLM_OPENAI_API_KEY", "sk-x")
        _configure_llm_auto(s)
        assert s.llm_provider == "openai"
        assert s.llm_api_key == "sk-x"

    def test_auto_no_key_leaves_default(self, tmp_path, monkeypatch):
        s = make_settings(tmp_path)
        monkeypatch.delenv("LLM_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("LLM_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_GROQ_API_KEY", raising=False)
        monkeypatch.delenv("LLM_GEMINI_API_KEY", raising=False)
        _configure_llm_auto(s)
        assert s.llm_provider == "anthropic"

    def test_interactive_no_existing_key(self, tmp_path, monkeypatch):
        s = make_settings(tmp_path)
        monkeypatch.delenv("LLM_ANTHROPIC_API_KEY", raising=False)
        with patch("honcho_pi.bootstrap.Prompt.ask", return_value="anthropic") as mock_prompt, patch(
            "honcho_pi.bootstrap.console.input", return_value="new-key"
        ):
            _configure_llm_interactive(s)
        assert s.llm_provider == "anthropic"
        assert s.llm_api_key == "new-key"

    def test_interactive_existing_key(self, tmp_path, monkeypatch):
        s = make_settings(tmp_path)
        monkeypatch.setenv("LLM_ANTHROPIC_API_KEY", "sk-existing-1234")
        with patch("honcho_pi.bootstrap.Prompt.ask", return_value="anthropic"), patch(
            "honcho_pi.bootstrap.console"
        ) as mock_console:
            _configure_llm_interactive(s)
        assert s.llm_api_key == "k"  # unchanged; existing env key not written to settings


class TestEmbeddingsConfig:
    def test_interactive_ollama(self, tmp_path):
        s = make_settings(tmp_path)
        with patch("honcho_pi.bootstrap.Prompt.ask", side_effect=["ollama", "http://localhost:11434"]) as mock_prompt, patch(
            "honcho_pi.bootstrap.Confirm.ask", return_value=True
        ), patch("honcho_pi.bootstrap._run_ollama_pull") as mock_pull:
            _configure_embeddings_interactive(s)
        assert s.embedding_provider == "ollama"
        assert s.ollama_url == "http://localhost:11434"
        mock_pull.assert_called_once_with("nomic-embed-text")

    def test_interactive_openai(self, tmp_path):
        s = make_settings(tmp_path)
        with patch("honcho_pi.bootstrap.Prompt.ask", return_value="openai"):
            _configure_embeddings_interactive(s)
        assert s.embedding_model == "text-embedding-3-small"


class TestRerankerConfig:
    def test_disabled(self, tmp_path):
        s = make_settings(tmp_path)
        with patch("honcho_pi.bootstrap.Confirm.ask", return_value=False):
            _configure_reranker_interactive(s)
        assert s.reranker_enabled is False

    def test_enabled_non_ollama(self, tmp_path):
        s = make_settings(tmp_path)
        s.embedding_provider = "openai"
        with patch("honcho_pi.bootstrap.Confirm.ask", return_value=True):
            _configure_reranker_interactive(s)
        assert s.reranker_enabled is True

    def test_enabled_ollama_pulls(self, tmp_path):
        s = make_settings(tmp_path)
        s.embedding_provider = "ollama"
        with patch("honcho_pi.bootstrap.Confirm.ask", side_effect=[True, True]), patch(
            "honcho_pi.bootstrap._run_ollama_pull"
        ) as mock_pull:
            _configure_reranker_interactive(s)
        mock_pull.assert_called_once_with("qllama/bge-reranker-large:f16")


class TestPiExtensionConfig:
    def test_pi_missing(self, tmp_path):
        s = make_settings(tmp_path)
        s.pi_agent_dir = tmp_path / "nope"
        with patch("honcho_pi.bootstrap.console") as mock_console:
            _configure_pi_extension_interactive(s)
        # returns early without asking

    def test_pi_present_install(self, tmp_path):
        s = make_settings(tmp_path)
        s.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        with patch("honcho_pi.bootstrap.Confirm.ask", return_value=True), patch(
            "honcho_pi.pi_integration.install_pi_extension"
        ) as mock_install:
            _configure_pi_extension_interactive(s)
        mock_install.assert_called_once_with(s)

    def test_pi_present_install_failure(self, tmp_path):
        s = make_settings(tmp_path)
        s.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        with patch("honcho_pi.bootstrap.Confirm.ask", return_value=True), patch(
            "honcho_pi.pi_integration.install_pi_extension", side_effect=RuntimeError("boom")
        ):
            _configure_pi_extension_interactive(s)  # should not raise


class TestServiceConfig:
    def test_interactive(self, tmp_path):
        s = make_settings(tmp_path)
        with patch("honcho_pi.bootstrap.IntPrompt.ask", return_value=9000) as mock_port, patch(
            "honcho_pi.bootstrap.Confirm.ask", return_value=True
        ):
            _configure_service_interactive(s)
        assert s.api_port == 9000
        assert s.dreaming_enabled is True


class TestSaveConfig:
    def test_saves(self, tmp_path):
        s = make_settings(tmp_path)
        with patch.object(s, "save_env_file") as mock_save:
            _save_configuration(s)
        mock_save.assert_called_once_with()

    def test_save_failure_raises(self, tmp_path):
        s = make_settings(tmp_path)
        with patch.object(s, "save_env_file", side_effect=OSError("boom")):
            with pytest.raises(BootstrapError):
                _save_configuration(s)


class TestCommandExists:
    def test_exists(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert _command_exists("docker") is True

    def test_missing(self):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, [])):
            assert _command_exists("docker") is False

    def test_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert _command_exists("docker") is False


class TestDockerPostgres:
    def test_already_running(self):
        with patch("subprocess.run") as mock_run:
            first = subprocess.CompletedProcess([], 0, stdout="container-id\n")
            mock_run.return_value = first
            _start_docker_postgres()
        # only the ps check ran
        assert mock_run.call_count == 1

    def test_starts_new_container(self):
        results = [
            subprocess.CompletedProcess([], 0, stdout=""),  # ps: none
            subprocess.CompletedProcess([], 0),  # rm
            subprocess.CompletedProcess([], 0),  # run
        ]
        with patch("subprocess.run", side_effect=results) as mock_run, patch("time.sleep"):
            _start_docker_postgres()
        assert mock_run.call_count == 3

    def test_run_failure_raises(self):
        results = [
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CompletedProcess([], 0),
            subprocess.CalledProcessError(1, []),
        ]
        with patch("subprocess.run", side_effect=results):
            with pytest.raises(BootstrapError):
                _start_docker_postgres()


class TestTestDbConnection:
    def test_success(self):
        with patch("psycopg.connect") as mock_connect:
            conn = mock_connect.return_value
            assert _test_database_connection("postgresql://x") is True
            conn.execute.assert_called_once_with("SELECT 1")
            conn.close.assert_called_once()

    def test_failure(self):
        with patch("psycopg.connect", side_effect=OSError("down")):
            assert _test_database_connection("postgresql://x") is False


class TestOllamaPull:
    def test_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            _run_ollama_pull("model-x")
        mock_run.assert_called_once()

    def test_failure_logged(self):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, [])), patch(
            "honcho_pi.bootstrap.console"
        ) as mock_console:
            _run_ollama_pull("model-x")  # no raise


class TestCheckPrerequisites:
    def test_ok(self, monkeypatch):
        with patch("honcho_pi.bootstrap._command_exists", return_value=True):
            issues = check_prerequisites()
        assert issues == []

    def test_missing_commands(self, monkeypatch):
        def fake_exists(cmd):
            return cmd not in ("curl", "docker", "systemctl")

        with patch("honcho_pi.bootstrap._command_exists", side_effect=fake_exists):
            issues = check_prerequisites()
        assert any("curl" in i for i in issues)
        assert any("Docker" in i for i in issues)
        assert any("systemd" in i for i in issues)
