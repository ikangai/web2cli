"""Proves the pytest harness is wired up, the shared fixtures import, and the
bare `from conftest import ...` style (used by every later component) resolves.
"""
import pathlib

import pytest


def test_pytest_runs():
    assert True


def test_bare_conftest_import_resolves():
    # Every later component uses `from conftest import TMUX, FAKE, requires_tmux`.
    # importmode=importlib + pythonpath in pytest.ini must make this work.
    from conftest import TMUX, FAKE, CAPTURES, requires_tmux  # noqa: F401
    assert "fake_claude.sh" in str(FAKE)
    assert str(CAPTURES).endswith("fixtures/captures")


def test_conftest_fixtures_exist(request):
    # All four shared fixtures from the plan contract must be registered.
    for name in ("tmp_base", "capture", "fake_socket", "fake_claude_argv"):
        assert _fixture_defined(request, name), (
            f"shared fixture {name!r} is not defined in conftest.py"
        )


def _fixture_defined(request, name):
    # _fixturemanager._arg2fixturedefs maps fixture name -> definitions.
    return name in request._fixturemanager._arg2fixturedefs


def test_tmp_base_is_0700_dir(tmp_base):
    assert tmp_base.is_dir()
    assert (tmp_base.stat().st_mode & 0o777) == 0o700


def test_repo_root_has_pytest_ini():
    root = pathlib.Path(__file__).resolve().parents[2]
    assert (root / "pytest.ini").is_file()
