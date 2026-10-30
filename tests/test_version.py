"""Unit tests for src/_version.py."""

from importlib.metadata import PackageNotFoundError

import pytest
import tomllib

import src._version as version_mod


def test_honcho_version_is_nonempty_string():
    assert isinstance(version_mod.HONCHO_VERSION, str)
    assert version_mod.HONCHO_VERSION


def test_read_version_success_from_pyproject(monkeypatch):
    monkeypatch.setattr(
        version_mod.tomllib, "load", lambda f: {"project": {"version": "1.2.3"}}
    )
    assert version_mod._read_version() == "1.2.3"


@pytest.mark.parametrize("exc", [OSError("missing"), KeyError("version"), tomllib.TOMLDecodeError("bad")])
def test_read_version_falls_back_to_package_metadata(monkeypatch, exc):
    monkeypatch.setattr(version_mod.tomllib, "load", lambda f: (_ for _ in ()).throw(exc))
    monkeypatch.setattr(version_mod, "_pkg_version", lambda name: "9.9.9")
    assert version_mod._read_version() == "9.9.9"


def test_read_version_unknown_when_no_package_metadata(monkeypatch):
    monkeypatch.setattr(version_mod.tomllib, "load", lambda f: (_ for _ in ()).throw(OSError("x")))

    def _raise(_name):
        raise PackageNotFoundError("not installed")

    monkeypatch.setattr(version_mod, "_pkg_version", _raise)
    assert version_mod._read_version() == "unknown"
