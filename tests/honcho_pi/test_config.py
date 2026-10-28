"""Unit tests for honcho_pi.config."""

import os
from pathlib import Path

import pytest

from honcho_pi.config import (
    ConfigError,
    Settings,
    _env_bool,
    _env_path,
)


class TestSettingsDefaults:
    def test_default_paths(self):
        s = Settings()
        assert s.api_port == 8333
        assert s.api_host == "127.0.0.1"
        assert s.llm_provider == "anthropic"
        assert s.embedding_provider == "openai"
        assert s.embedding_model == "text-embedding-3-small"
        assert s.reranker_enabled is False
        assert s.dreaming_enabled is True

    def test_post_init_derives_env_file(self):
        s = Settings()
        assert s.env_file == s.config_dir / ".env"
        assert s.honcho_source_dir == s.honcho_install_dir / "honcho"

    def test_post_init_respects_explicit_env_file(self):
        s = Settings(env_file=Path("/tmp/custom.env"))
        assert s.env_file == Path("/tmp/custom.env")


class TestFromEnv:
    def test_env_overrides(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HONCHO_PI_PORT", "9999")
        monkeypatch.setenv("HONCHO_PI_HOST", "0.0.0.0")
        monkeypatch.setenv("HONCHO_PI_ENV_FILE", str(tmp_path / "e.env"))
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
        monkeypatch.setenv("RERANKER_ENABLED", "true")
        monkeypatch.setenv("DREAMING_ENABLED", "false")

        s = Settings.from_env()
        assert s.api_port == 9999
        assert s.api_host == "0.0.0.0"
        assert s.env_file == tmp_path / "e.env"
        assert s.llm_provider == "openai"
        assert s.llm_api_key == "sk-test"
        assert s.embedding_provider == "ollama"
        assert s.reranker_enabled is True
        assert s.dreaming_enabled is False

    def test_from_env_loads_env_file(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_TEST_VAR=hello\n# comment\n\n")
        monkeypatch.setenv("HONCHO_PI_ENV_FILE", str(env_file))
        # from_env will call load_env_file which uses os.environ.setdefault
        Settings.from_env()
        assert os.environ.get("MY_TEST_VAR") == "hello"


class TestLoadSaveEnvFile:
    def test_load_env_file_missing_is_noop(self, tmp_path):
        s = Settings()
        s.load_env_file(tmp_path / "nope.env")  # should not raise

    def test_load_env_file_parses(self, tmp_path):
        f = tmp_path / "e.env"
        f.write_text("FOO=bar\nSKIP\n# comment\nBAZ=qux\n")
        s = Settings()
        s.load_env_file(f)
        assert os.environ.get("FOO") == "bar"
        assert os.environ.get("BAZ") == "qux"

    def test_load_env_file_does_not_override_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FOO", "already")
        f = tmp_path / "e.env"
        f.write_text("FOO=new\n")
        s = Settings()
        s.load_env_file(f)
        assert os.environ.get("FOO") == "already"

    def test_save_env_file_writes_and_chmod(self, tmp_path):
        s = Settings(
            config_dir=tmp_path,
            api_port=9000,
            database_url="postgresql://x",
            llm_provider="anthropic",
            llm_api_key="secret-key",
            embedding_provider="openai",
            reranker_enabled=True,
            ollama_url="http://localhost:11434",
        )
        f = tmp_path / ".env"
        s.save_env_file(f)
        content = f.read_text()
        assert "API_PORT=9000" in content
        assert "LLM_PROVIDER=anthropic" in content
        assert f"LLM_ANTHROPIC_API_KEY=***" in content
        assert "RERANKER_ENABLED=true" in content
        assert "LLM_OLLAMA_BASE_URL=http://localhost:11434" in content
        assert "DREAMING_ENABLED=true" in content
        assert f.stat().st_mode & 0o777 == 0o600

    def test_save_env_file_none_path_uses_env_file(self, tmp_path):
        s = Settings(config_dir=tmp_path)
        s.save_env_file(None)
        assert s.env_file.exists()


class TestEnsureDirs:
    def test_creates_directories(self, tmp_path):
        s = Settings(
            config_dir=tmp_path / "config",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            honcho_install_dir=tmp_path / "install",
            pi_extensions_dir=tmp_path / "ext",
        )
        s.ensure_directories()
        for d in [s.config_dir, s.data_dir, s.log_dir, s.honcho_install_dir, s.pi_extensions_dir]:
            assert d.exists()


class TestValidate:
    def test_missing_database_error(self, tmp_path):
        s = Settings(honcho_install_dir=tmp_path, llm_api_key="k")
        errors = s.validate()
        assert any("Database URL" in e for e in errors)

    def test_docker_db_allows_missing_url(self, tmp_path):
        s = Settings(
            honcho_install_dir=tmp_path,
            use_docker_db=True,
            llm_api_key="k",
        )
        errors = s.validate()
        assert not any("Database URL" in e for e in errors)

    def test_llm_key_required_for_anthropic(self, tmp_path):
        s = Settings(honcho_install_dir=tmp_path, database_url="x", llm_provider="anthropic")
        errors = s.validate()
        assert any("API key is required" in e for e in errors)

    def test_missing_install_dir(self, tmp_path):
        s = Settings(database_url="x", llm_api_key="k", honcho_install_dir=tmp_path / "nope")
        errors = s.validate()
        assert any("install directory" in e for e in errors)

    def test_port_in_use(self, tmp_path, monkeypatch):
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            s = Settings(
                honcho_install_dir=tmp_path,
                database_url="x",
                llm_api_key="k",
                api_host="127.0.0.1",
                api_port=port,
            )
            errors = s.validate()
            assert any("already in use" in e for e in errors)
        finally:
            sock.close()


class TestHelpers:
    def test_env_path_with_env(self, monkeypatch):
        monkeypatch.setenv("MY_PATH", "/tmp/x")
        assert _env_path("MY_PATH", Path("/default")) == Path("/tmp/x")

    def test_env_path_default(self):
        assert _env_path("MY_PATH_DOES_NOT_EXIST", Path("/default")) == Path("/default")

    def test_env_bool_true_values(self, monkeypatch):
        for v in ["true", "TRUE", "1", "yes", "on"]:
            monkeypatch.setenv("MY_BOOL", v)
            assert _env_bool("MY_BOOL", False) is True

    def test_env_bool_false_values(self, monkeypatch):
        for v in ["false", "0", "no", "off", "random"]:
            monkeypatch.setenv("MY_BOOL", v)
            assert _env_bool("MY_BOOL", False) is False

    def test_env_bool_default(self, monkeypatch):
        monkeypatch.delenv("MY_BOOL", raising=False)
        assert _env_bool("MY_BOOL", True) is True


class TestConfigError:
    def test_is_exception(self):
        with pytest.raises(ConfigError):
            raise ConfigError("boom")
