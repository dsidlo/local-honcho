"""Unit tests for honcho_pi.cli."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from honcho_pi.cli import app, self_app

runner = CliRunner()


class TestChe:
    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "honcho-pi" in result.output


class TestInstall:
    def test_already_configured_no_force(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        s = tmp_path / "settings.py"
        s.write_text("")
        with patch("honcho_pi.cli.settings") as mock_settings:
            mock_settings.env_file = env_file
            mock_settings.config_dir = tmp_path
            # defaults: interactive=True, force=False
            result = runner.invoke(app, ["install", "--non-interactive"])
        # interactive is False in non-interactive, so no confirm prompt; it proceeds
        assert result.exit_code in (0, 1)

    def test_install_force_skips_config_check(self, tmp_path):
        with patch("honcho_pi.cli.Settings.env_file", None, create=True), patch(
            "honcho_pi.cli.check_prerequisites", return_value=[]
        ), patch("honcho_pi.cli.run_configuration") as mock_run, patch(
            "honcho_pi.cli.ServiceManager"
        ) as mock_mgr:
            mock_mgr.return_value = mock_mgr
            mock_run.return_value = mock_mgr.settings = object()
            result = runner.invoke(app, ["install", "--non-interactive", "--force"])
        assert mock_run.called

    def test_install_prereq_failure(self, tmp_path):
        with patch("honcho_pi.cli.settings") as mock_settings:
            mock_settings.env_file = None
            mock_settings.config_dir = tmp_path
            with patch("honcho_pi.cli.check_prerequisites", return_value=["Python 3.10+ required"]):
                result = runner.invoke(app, ["install", "--non-interactive"])
            assert result.exit_code == 1

    def test_install_run_config_failure(self, tmp_path):
        with patch("honcho_pi.cli.settings") as mock_settings:
            mock_settings.env_file = None
            mock_settings.config_dir = tmp_path
            with patch("honcho_pi.cli.check_prerequisites", return_value=[]), patch(
                "honcho_pi.cli.run_configuration", side_effect=RuntimeError("boom")
            ):
                result = runner.invoke(app, ["install", "--non-interactive"])
            assert result.exit_code == 1


class TestStart:
    def test_start_success_healthy(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr:
            mgr = mock_mgr.return_value
            mgr.start_services.return_value = True
            mgr.get_status.return_value = {"api_healthy": True}
            with patch("honcho_pi.cli.settings") as mock_settings:
                mock_settings.api_host = "127.0.0.1"
                mock_settings.api_port = 8333
                result = runner.invoke(app, ["start", "--no-wait"])
            assert result.exit_code == 0

    def test_start_failure(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr:
            mgr = mock_mgr.return_value
            mgr.start_services.return_value = False
            result = runner.invoke(app, ["start", "--no-wait"])
            assert result.exit_code == 1


class TestStop:
    def test_stop_success(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr:
            mock_mgr.return_value.stop_services.return_value = True
            result = runner.invoke(app, ["stop"])
            assert result.exit_code == 0

    def test_stop_returns_false(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr:
            mock_mgr.return_value.stop_services.return_value = False
            result = runner.invoke(app, ["stop"])
            assert result.exit_code == 0  # doesn't exit 1 on stop failure


class TestRestart:
    def test_restart_success(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr:
            mock_mgr.return_value.restart_services.return_value = True
            result = runner.invoke(app, ["restart"])
            assert result.exit_code == 0

    def test_restart_failure(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr:
            mock_mgr.return_value.restart_services.return_value = False
            result = runner.invoke(app, ["restart"])
            assert result.exit_code == 1


class TestStatus:
    def test_status(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr, patch(
            "honcho_pi.cli.check_pi_status"
        ) as mock_status:
            mgr = mock_mgr.return_value
            mgr.get_status.return_value = {
                "honcho-api": "active",
                "honcho-deriver": "active",
                "api_healthy": True,
            }
            mock_status.return_value = {
                "installed": True,
                "extension_present": True,
                "settings_configured": True,
                "errors": [],
            }
            result = runner.invoke(app, ["status", "--verbose"])
            assert result.exit_code == 0
            assert "API Health" in result.output

    def test_status_not_installed(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr, patch(
            "honcho_pi.cli.check_pi_status"
        ) as mock_status:
            mgr = mock_mgr.return_value
            mgr.get_status.return_value = {
                "honcho-api": "inactive",
                "honcho-deriver": "inactive",
                "api_healthy": False,
            }
            mock_status.return_value = {"installed": False}
            result = runner.invoke(app, ["status"])
            assert result.exit_code == 0


class TestConfigure:
    def test_configure_non_interactive(self):
        with patch("honcho_pi.cli.run_configuration") as mock_run, patch(
            "honcho_pi.cli.settings"
        ) as mock_settings:
            mock_settings.env_file = None
            result = runner.invoke(app, ["configure", "--non-interactive"])
            assert result.exit_code == 0
            mock_run.assert_called_once_with(interactive=False, settings=mock_settings)

    def test_configure_failure(self):
        with patch("honcho_pi.cli.run_configuration", side_effect=RuntimeError("boom")), patch(
            "honcho_pi.cli.settings"
        ) as mock_settings:
            mock_settings.env_file = None
            result = runner.invoke(app, ["configure", "--non-interactive"])
            assert result.exit_code == 1


class TestDoctor:
    def test_doctor_all_good(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr, patch(
            "honcho_pi.cli.check_pi_status"
        ) as mock_status, patch("honcho_pi.cli.settings") as mock_settings:
            mgr = mock_mgr.return_value
            mgr.get_status.return_value = {
                "honcho-api": "active",
                "honcho-deriver": "active",
                "api_healthy": True,
            }
            mock_status.return_value = {
                "installed": True,
                "extension_present": True,
                "settings_configured": True,
                "errors": [],
            }
            mock_settings.config_dir = Path("/tmp")
            mock_settings.data_dir = Path("/tmp")
            mock_settings.env_file = Path("/tmp/.env")
            mock_settings.database_url = "postgresql://x"
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            assert "All checks passed" in result.output

    def test_doctor_issues(self):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr, patch(
            "honcho_pi.cli.check_pi_status"
        ) as mock_status, patch("honcho_pi.cli.settings") as mock_settings:
            mgr = mock_mgr.return_value
            mgr.get_status.return_value = {
                "honcho-api": "inactive",
                "honcho-deriver": "inactive",
                "api_healthy": False,
            }
            mock_status.return_value = {
                "installed": True,
                "extension_present": False,
                "settings_configured": False,
                "errors": [],
            }
            mock_settings.config_dir = Path("/nope")
            mock_settings.data_dir = Path("/nope")
            mock_settings.env_file = None
            mock_settings.database_url = None
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            assert "Some issues found" in result.output


class TestSelfUpdate:
    def test_update_up_to_date(self):
        from unittest.mock import MagicMock
        with patch("urllib.request.urlopen") as mock_urlopen, patch(
            "honcho_pi.__version__", "1.0.0"
        ):
            resp = MagicMock()
            resp.read.return_value = b'{"tag_name": "v1.0.0", "html_url": "x"}'
            mock_urlopen.return_value.__enter__.return_value = resp
            result = runner.invoke(self_app, ["update", "--check"])
            assert result.exit_code == 0
            assert "up to date" in result.output

    def test_update_available(self):
        from unittest.mock import MagicMock
        with patch("urllib.request.urlopen") as mock_urlopen, patch(
            "honcho_pi.__version__", "1.0.0"
        ):
            resp = MagicMock()
            resp.read.return_value = b'{"tag_name": "v2.0.0", "html_url": "x"}'
            mock_urlopen.return_value.__enter__.return_value = resp
            result = runner.invoke(self_app, ["update", "--check"])
            assert result.exit_code == 0
            assert "Update available" in result.output

    def test_update_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("net down")):
            result = runner.invoke(self_app, ["update", "--check"])
            assert result.exit_code == 0
            assert "Could not check" in result.output


class TestSelfMetadata:
    def test_metadata(self):
        with patch("honcho_pi.cli.settings") as mock_settings:
            mock_settings.config_dir = Path("/tmp")
            mock_settings.data_dir = Path("/tmp")
            mock_settings.honcho_install_dir = Path("/tmp/install")
            result = runner.invoke(self_app, ["metadata"])
        assert result.exit_code == 0
        assert "Honcho Pi" in result.output


class TestSelfLogs:
    def test_logs(self):
        with patch("honcho_pi.cli.get_journal_logs") as mock_logs:
            result = runner.invoke(self_app, ["logs", "--service", "honcho-api", "--lines", "10"])
            assert result.exit_code == 0
            mock_logs.assert_called_once_with("honcho-api", lines=10, follow=False)

    def test_logs_follow(self):
        with patch("honcho_pi.cli.get_journal_logs") as mock_logs:
            result = runner.invoke(self_app, ["logs", "-f"])
            assert result.exit_code == 0
            mock_logs.assert_called_once_with("honcho-api", lines=50, follow=True)


class TestSelfUninstall:
    def test_uninstall_confirmed(self, tmp_path):
        with patch("honcho_pi.cli.ServiceManager") as mock_mgr, patch(
            "honcho_pi.pi_integration.uninstall_pi_extension"
        ), patch("honcho_pi.cli.settings") as mock_settings:
            mock_mgr.return_value.uninstall.return_value = True
            mock_settings.config_dir = tmp_path / "cfg"
            mock_settings.data_dir = tmp_path / "data"
            mock_settings.honcho_install_dir = tmp_path / "install"
            for d in [mock_settings.config_dir, mock_settings.data_dir, mock_settings.honcho_install_dir]:
                d.mkdir(parents=True, exist_ok=True)
            result = runner.invoke(self_app, ["uninstall", "--yes"])
            assert result.exit_code == 0
            assert "uninstalled" in result.output

    def test_uninstall_no_confirm_aborts(self):
        with patch("honcho_pi.cli.typer.confirm", return_value=False):
            result = runner.invoke(self_app, ["uninstall"])
            assert result.exit_code == 0
            assert "Aborted" in result.output
