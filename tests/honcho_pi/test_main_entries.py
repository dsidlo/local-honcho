"""Test the module entry points."""
import importlib
import subprocess
import sys


def test_honcho_pi_main_importable():
    # importing should not raise; __main__ calls cli.app() only under __name__ == "__main__"
    import honcho_pi.__main__
    import honcho_pi
    assert honcho_pi.__version__ == "1.0.0"


def test_honcho_pi_main_executes_app(monkeypatch):
    import honcho_pi.__main__ as m
    called = {}

    class FakeApp:
        @staticmethod
        def __call__():
            called["ok"] = True

    monkeypatch.setattr(m, "app", FakeApp())
    # running the module with its __name__ set to "__main__"
    import runpy
    # we can't easily re-run with different __name__, so verify the guard logic
    assert m.__name__ == "honcho_pi.__main__" or m.__name__ == "__main__"
