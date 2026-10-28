"""Unit tests for honcho_pi.pi_integration."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from honcho_pi.config import Settings
from honcho_pi.pi_integration import (
    DEFAULT_HONCHO_SETTINGS,
    HONCHO_TS_TEMPLATE,
    PiIntegration,
    PiIntegrationError,
    check_pi_status,
    install_pi_extension,
    uninstall_pi_extension,
)


def make_settings(tmp_path) -> Settings:
    cfg = Settings(
        config_dir=tmp_path / "config",
        pi_agent_dir=tmp_path / "pi_agent",
        pi_extensions_dir=tmp_path / "pi_agent" / "extensions",
        api_port=8333,
    )
    cfg.pi_extensions_dir.mkdir(parents=True, exist_ok=True)
    return cfg


class TestPiIntegration:
    def _pi(self, tmp_path):
        return PiIntegration(make_settings(tmp_path))

    def test_is_pi_installed_false(self, tmp_path):
        assert self._pi(tmp_path).is_pi_installed() is False

    def test_is_pi_installed_true(self, tmp_path):
        pi = self._pi(tmp_path)
        pi.settings.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        (pi.settings.pi_agent_dir / "settings.json").write_text("{}")
        assert pi.is_pi_installed() is True

    def test_get_pi_settings_path(self, tmp_path):
        pi = self._pi(tmp_path)
        assert pi.get_pi_settings_path() == pi.settings.pi_agent_dir / "settings.json"

    def test_get_honcho_extension_path(self, tmp_path):
        pi = self._pi(tmp_path)
        assert pi.get_honcho_extension_path() == pi.settings.pi_extensions_dir / "honcho.ts"

    def test_read_pi_settings_missing_returns_empty(self, tmp_path):
        assert self._pi(tmp_path).read_pi_settings() == {}

    def test_read_pi_settings_valid(self, tmp_path):
        pi = self._pi(tmp_path)
        pi.settings.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        (pi.settings.pi_agent_dir / "settings.json").write_text('{"a": 1}')
        assert pi.read_pi_settings() == {"a": 1}

    def test_read_pi_settings_invalid_json_raises(self, tmp_path):
        pi = self._pi(tmp_path)
        pi.settings.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        (pi.settings.pi_agent_dir / "settings.json").write_text("{invalid")
        with pytest.raises(PiIntegrationError):
            pi.read_pi_settings()

    def test_write_pi_settings_creates_backup(self, tmp_path):
        pi = self._pi(tmp_path)
        pi.settings.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        existing = pi.settings.pi_agent_dir / "settings.json"
        existing.write_text('{"old": true}')
        pi.write_pi_settings({"new": True}, backup=True)
        assert json.loads(existing.read_text()) == {"new": True}
        backup_path = existing.with_suffix(".json.backup")
        assert backup_path.exists()
        assert json.loads(backup_path.read_text()) == {"old": True}

    def test_write_pi_settings_no_backup(self, tmp_path):
        pi = self._pi(tmp_path)
        pi.settings.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        existing = pi.settings.pi_agent_dir / "settings.json"
        existing.write_text('{"old": true}')
        pi.write_pi_settings({"new": True}, backup=False)
        assert not existing.with_suffix(".json.backup").exists()

    def test_merge_honcho_settings_new(self, tmp_path):
        pi = self._pi(tmp_path)
        merged = pi.merge_honcho_settings({})
        assert merged["honcho"]["enabled"] is True
        assert merged["honcho"]["api_url"] == f"http://localhost:{pi.settings.api_port}"

    def test_merge_honcho_settings_updates_existing(self, tmp_path):
        pi = self._pi(tmp_path)
        merged = pi.merge_honcho_settings({"honcho": {"enabled": False, "api_url": "x"}})
        assert merged["honcho"]["enabled"] is True
        assert merged["honcho"]["api_url"] == f"http://localhost:{pi.settings.api_port}"

    def test_generate_honcho_ts(self, tmp_path, monkeypatch):
        pi = self._pi(tmp_path)
        monkeypatch.setenv("HONCHO_WORKSPACE", "ws")
        monkeypatch.setenv("USER", "bob")
        monkeypatch.setenv("HONCHO_AGENT_ID", "agent-1")
        with patch(
            "honcho_pi.pi_integration.HONCHO_TS_TEMPLATE",
            "port={port}|workspace={workspace}|user={user_id}|agent={agent_id}",
        ):
            content = pi.generate_honcho_ts()
        assert "8333" in content
        assert "ws" in content
        assert "bob" in content
        assert "agent-1" in content

    def test_install_extension_not_installed_raises(self, tmp_path):
        with pytest.raises(PiIntegrationError):
            self._pi(tmp_path).install_extension()

    def test_install_extension_success(self, tmp_path):
        pi = self._pi(tmp_path)
        pi.settings.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        (pi.settings.pi_agent_dir / "settings.json").write_text("{}")
        with patch(
            "honcho_pi.pi_integration.HONCHO_TS_TEMPLATE",
            "port={port}|workspace={workspace}|user={user_id}|agent={agent_id}",
        ):
            assert pi.install_extension() is True
        ext = pi.get_honcho_extension_path()
        assert ext.exists()
        assert "8333" in ext.read_text()
        settings = pi.read_pi_settings()
        assert settings["honcho"]["enabled"] is True

    def test_uninstall_extension(self, tmp_path):
        pi = self._pi(tmp_path)
        pi.settings.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        ext = pi.get_honcho_extension_path()
        ext.write_text("x")
        (pi.settings.pi_agent_dir / "settings.json").write_text(
            json.dumps(DEFAULT_HONCHO_SETTINGS)
        )
        assert pi.uninstall_extension() is True
        assert not ext.exists()
        assert "honcho" not in pi.read_pi_settings()

    def test_validate_extension_pi_not_installed(self, tmp_path):
        errors = self._pi(tmp_path).validate_extension()
        assert any("not installed" in e for e in errors)

    def test_validate_extension_ok(self, tmp_path):
        pi = self._pi(tmp_path)
        settings_dir = pi.settings.pi_agent_dir
        settings_dir.mkdir(parents=True, exist_ok=True)
        # settings.json + extension + api reachable
        cfg = {"honcho": {"enabled": True, "api_url": f"http://localhost:{pi.settings.api_port}"}}
        (settings_dir / "settings.json").write_text(json.dumps(cfg))
        pi.get_honcho_extension_path().write_text("")
        with patch("urllib.request.urlopen") as mock_urlopen:
            class FakeResp:
                status = 200
            mock_urlopen.return_value.__enter__.return_value = FakeResp()
            errors = pi.validate_extension()
        assert errors == []

    def test_validate_extension_missing_extension(self, tmp_path):
        pi = self._pi(tmp_path)
        settings_dir = pi.settings.pi_agent_dir
        settings_dir.mkdir(parents=True, exist_ok=True)
        cfg = {"honcho": {"enabled": True}}
        (settings_dir / "settings.json").write_text(json.dumps(cfg))
        errors = pi.validate_extension()
        assert any("Extension not found" in e for e in errors)

    def test_validate_extension_disabled(self, tmp_path):
        pi = self._pi(tmp_path)
        settings_dir = pi.settings.pi_agent_dir
        settings_dir.mkdir(parents=True, exist_ok=True)
        cfg = {"honcho": {"enabled": False}}
        (settings_dir / "settings.json").write_text(json.dumps(cfg))
        pi.get_honcho_extension_path().write_text("")
        errors = pi.validate_extension()
        assert any("disabled" in e for e in errors)

    def test_validate_extension_api_unreachable(self, tmp_path):
        pi = self._pi(tmp_path)
        settings_dir = pi.settings.pi_agent_dir
        settings_dir.mkdir(parents=True, exist_ok=True)
        cfg = {"honcho": {"enabled": True, "api_url": "http://localhost:1"}}
        (settings_dir / "settings.json").write_text(json.dumps(cfg))
        pi.get_honcho_extension_path().write_text("")
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            errors = pi.validate_extension()
        assert any("Cannot connect" in e for e in errors)

    def test_restart_pi(self, tmp_path):
        pi = self._pi(tmp_path)
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.return_value = None
            assert pi.restart_pi() is True
        mock_run.assert_called_once()

    def test_restart_pi_failure(self, tmp_path):
        pi = self._pi(tmp_path)
        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            assert pi.restart_pi() is False


class TestConvenience:
    def test_install_pi_extension(self, tmp_path):
        s = make_settings(tmp_path)
        s.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        (s.pi_agent_dir / "settings.json").write_text("{}")
        with patch(
            "honcho_pi.pi_integration.HONCHO_TS_TEMPLATE",
            "port={port}|workspace={workspace}|user={user_id}|agent={agent_id}",
        ):
            assert install_pi_extension(s) is True

    def test_uninstall_pi_extension(self, tmp_path):
        s = make_settings(tmp_path)
        s.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        (s.pi_agent_dir / "extensions").mkdir(parents=True, exist_ok=True)
        (s.pi_agent_dir / "extensions" / "honcho.ts").write_text("")
        (s.pi_agent_dir / "settings.json").write_text(
            json.dumps(DEFAULT_HONCHO_SETTINGS)
        )
        assert uninstall_pi_extension(s) is True

    def test_check_pi_status_not_installed(self, tmp_path):
        status = check_pi_status(make_settings(tmp_path))
        assert status["installed"] is False
        assert status["errors"] == ["Pi not installed"]

    def test_check_pi_status_installed(self, tmp_path):
        s = make_settings(tmp_path)
        s.pi_agent_dir.mkdir(parents=True, exist_ok=True)
        cfg = {"honcho": {"enabled": True, "api_url": "http://localhost:8333"}}
        (s.pi_agent_dir / "settings.json").write_text(json.dumps(cfg))
        (s.pi_agent_dir / "extensions" / "honcho.ts").write_text("")
        with patch("urllib.request.urlopen") as mock_urlopen:
            class FakeResp:
                status = 200
            mock_urlopen.return_value.__enter__.return_value = FakeResp()
            status = check_pi_status(s)
        assert status["installed"] is True
        assert status["extension_present"] is True
        assert status["settings_configured"] is True


class TestPiIntegrationError:
    def test_is_exception(self):
        with pytest.raises(PiIntegrationError):
            raise PiIntegrationError("boom")
