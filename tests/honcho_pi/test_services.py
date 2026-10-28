"""Unit tests for honcho_pi.services."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from honcho_pi.config import Settings
from honcho_pi.services import (
    HONCHO_API_SERVICE_TEMPLATE,
    HONCHO_DERIVER_SERVICE_TEMPLATE,
    ServiceError,
    ServiceManager,
    get_journal_logs,
)


def make_settings(tmp_path) -> Settings:
    # Point important dirs into tmp_path to avoid touching $HOME
    cfg = Settings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        honcho_install_dir=tmp_path / "install",
        honcho_source_dir=tmp_path / "src_home",
        api_host="127.0.0.1",
        api_port=8888,
    )
    cfg.honcho_source_dir.mkdir(parents=True, exist_ok=True)
    (cfg.honcho_source_dir / "src").mkdir(parents=True, exist_ok=True)
    (cfg.honcho_source_dir / "src" / "main.py").write_text("")
    return cfg


class TestInit:
    def test_defaults(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYAPP", raising=False)
        with patch("honcho_pi.services.Settings.from_env", return_value=make_settings(tmp_path)):
            sm = ServiceManager()
        assert sm.pyapp_enabled is False
        assert sm.install_dir == sm.settings.honcho_install_dir
        assert sm.service_dir == Path.home() / ".config" / "systemd" / "user"

    def test_pyapp_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYAPP", "1")
        monkeypatch.setenv("INSTALL_DIR_HONCHO_PI", str(tmp_path / "pyapp"))
        with patch("honcho_pi.services.Settings.from_env", return_value=make_settings(tmp_path)):
            sm = ServiceManager()
        assert sm.pyapp_enabled is True
        assert sm.install_dir == tmp_path / "pyapp"


class TestGetUvPath:
    def test_finds_common_path(self, tmp_path, monkeypatch):
        cargo = tmp_path / ".cargo" / "bin" / "uv"
        cargo.parent.mkdir(parents=True)
        cargo.write_text("")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        sm = ServiceManager.__new__(ServiceManager)
        assert sm._get_uv_path() == str(cargo)

    def test_falls_back_to_which(self, tmp_path, monkeypatch):
        # Point $HOME at an empty tmp_path so the common uv locations don't exist
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        sm = ServiceManager.__new__(ServiceManager)

        def fake_run(cmd, **kw):
            assert cmd[0] == "which"
            return subprocess.CompletedProcess(cmd, 0, stdout="/usr/bin/uv")

        with patch("subprocess.run", side_effect=fake_run):
            assert sm._get_uv_path() == "/usr/bin/uv"

    def test_falls_back_to_uv_string(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        sm = ServiceManager.__new__(ServiceManager)

        def fake_run(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd)

        with patch("subprocess.run", side_effect=fake_run):
            assert sm._get_uv_path() == "uv"


class TestGenerateServices:
    def _fresh_sm(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYAPP", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("honcho_pi.services.Settings.from_env", return_value=make_settings(tmp_path)):
            sm = ServiceManager()
        return sm

    def test_generates_both_files(self, tmp_path, monkeypatch):
        sm = self._fresh_sm(tmp_path, monkeypatch)
        with patch.object(sm, "_get_uv_path", return_value="/bin/uv"):
            api_path, deriver_path = sm.generate_service_files()
        assert api_path.exists() and deriver_path.exists()
        api_content = api_path.read_text()
        assert "Honcho API Server" in api_content
        assert "ExecStart=/bin/uv run --no-dev fastapi dev" in api_content
        assert f"--host {sm.settings.api_host}" in api_content
        assert f"--port {sm.settings.api_port}" in api_content
        deriver_content = deriver_path.read_text()
        assert "Honcho Deriver" in deriver_content
        assert "python -m src.deriver" in deriver_content

    def test_pyapp_generation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYAPP", "1")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("honcho_pi.services.Settings.from_env", return_value=make_settings(tmp_path)):
            sm = ServiceManager()
        # Simulate bootstrap.py present
        (sm.settings.honcho_source_dir / "bootstrap.py").write_text("")
        with patch.object(sm, "_get_uv_path", return_value="/bin/uv"):
            api_path, deriver_path = sm.generate_service_files()
        assert "src.deriver" not in deriver_path.read_text()
        assert "python -m deriver" in deriver_path.read_text()


class TestDaemonCommands:
    def _sm(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYAPP", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("honcho_pi.services.Settings.from_env", return_value=make_settings(tmp_path)):
            return ServiceManager()

    def test_daemon_reload(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert sm.daemon_reload() is True
        mock_run.assert_called_once()

    def test_daemon_reload_failure(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, [])):
            assert sm.daemon_reload() is False

    def test_enable_services(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert sm.enable_services() is True
        assert mock_run.call_count == 2

    def test_enable_services_failure(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, [])):
            assert sm.enable_services() is False

    def test_start_services(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert sm.start_services() is True
        assert mock_run.call_count == 2

    def test_start_services_failure(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, [])):
            assert sm.start_services() is False

    def test_stop_services(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert sm.stop_services() is True
        assert mock_run.call_count == 2

    def test_restart_services(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert sm.restart_services() is True
        assert mock_run.call_count == 2

    def test_restart_services_failure(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, [])):
            assert sm.restart_services() is False


class TestStatus:
    def test_get_status_healthy(self, tmp_path, monkeypatch):
        sm = ServiceManager.__new__(ServiceManager)
        sm.settings = make_settings(tmp_path)

        def fake_run(cmd, **kw):
            if cmd[0] == "systemctl":
                return subprocess.CompletedProcess(cmd, 0, stdout="active")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("subprocess.run", side_effect=fake_run), patch(
            "urllib.request.urlopen"
        ) as mock_urlopen:
            class FakeResp:
                status = 200

            mock_urlopen.return_value.__enter__.return_value = FakeResp()
            status = sm.get_status()
        assert status["honcho-api"] == "active"
        assert status["honcho-deriver"] == "active"
        assert status["api_healthy"] is True

    def test_get_status_api_down(self, tmp_path, monkeypatch):
        sm = ServiceManager.__new__(ServiceManager)
        sm.settings = make_settings(tmp_path)

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="inactive")

        with patch("subprocess.run", side_effect=fake_run), patch(
            "urllib.request.urlopen", side_effect=OSError("down")
        ):
            status = sm.get_status()
        assert status["honcho-api"] == "inactive"
        assert status["api_healthy"] is False


class TestInstallUninstall:
    def _sm(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYAPP", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("honcho_pi.services.Settings.from_env", return_value=make_settings(tmp_path)):
            return ServiceManager()

    def test_install_and_start_full_flow(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch.object(sm, "_get_uv_path", return_value="/bin/uv"), patch(
            "subprocess.run"
        ) as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert sm.install_and_start() is True
        assert mock_run.call_count >= 1

    def test_install_and_start_stops_on_reload_fail(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch.object(sm, "_get_uv_path", return_value="/bin/uv"), patch.object(
            sm, "daemon_reload", return_value=False
        ), patch.object(sm, "generate_service_files"):
            assert sm.install_and_start() is False

    def test_uninstall(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        # create service files that should be removed
        sm.service_dir.mkdir(parents=True, exist_ok=True)
        (sm.service_dir / "honcho-api.service").write_text("x")
        (sm.service_dir / "honcho-deriver.service").write_text("x")
        with patch("subprocess.run") as mock_run, patch.object(sm, "daemon_reload"):
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            assert sm.uninstall() is True
        assert not (sm.service_dir / "honcho-api.service").exists()
        assert not (sm.service_dir / "honcho-deriver.service").exists()

    def test_uninstall_handles_errors(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            assert sm.uninstall() is False


class TestJournalLogs:
    def test_get_journal_logs(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            get_journal_logs("honcho-api", lines=10)
        args = mock_run.call_args[0][0]
        assert args[0] == "journalctl"
        assert "-n10" in args

    def test_get_journal_logs_follow(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            get_journal_logs("honcho-api", lines=10, follow=True)
        args = mock_run.call_args[0][0]
        assert "-f" in args

    def test_get_journal_logs_suppresses_keyboard_interrupt(self):
        with patch("subprocess.run", side_effect=KeyboardInterrupt()):
            get_journal_logs("honcho-api")  # should not raise


class TestServiceError:
    def test_is_exception(self):
        with pytest.raises(ServiceError):
            raise ServiceError("boom")
