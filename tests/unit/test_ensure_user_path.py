"""ensure_user_path() repairs the stripped-down PATH a GUI-launched .app
inherits (/usr/bin:/bin:/usr/sbin:/sbin) so spawned `claude`/`tmux` resolve
instead of failing with exit 127 (command not found)."""
import os

import bridge_common


def test_prepends_missing_real_dir(tmp_path, monkeypatch):
    real = tmp_path / "userbin"
    real.mkdir()
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(bridge_common, "_FALLBACK_BIN_DIRS", ())
    monkeypatch.setattr(
        bridge_common, "_harvest_login_path", lambda timeout=3.0: str(real)
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    added = bridge_common.ensure_user_path()

    assert added == [str(real)]
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[0] == str(real)              # prepended -> takes precedence
    assert parts[-2:] == ["/usr/bin", "/bin"]  # original entries preserved, after


def test_idempotent(tmp_path, monkeypatch):
    real = tmp_path / "userbin"
    real.mkdir()
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(bridge_common, "_FALLBACK_BIN_DIRS", ())
    monkeypatch.setattr(
        bridge_common, "_harvest_login_path", lambda timeout=3.0: str(real)
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    bridge_common.ensure_user_path()
    before = os.environ["PATH"]
    added_again = bridge_common.ensure_user_path()

    assert added_again == []                  # nothing new on a second pass
    assert os.environ["PATH"] == before


def test_skips_nonexistent_dir(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(bridge_common, "_FALLBACK_BIN_DIRS", ())
    monkeypatch.setattr(
        bridge_common, "_harvest_login_path", lambda timeout=3.0: "/no/such/dir/xyz"
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    added = bridge_common.ensure_user_path()

    assert added == []
    assert os.environ["PATH"] == "/usr/bin:/bin"


def test_skips_dir_already_on_path(tmp_path, monkeypatch):
    real = tmp_path / "userbin"
    real.mkdir()
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(bridge_common, "_FALLBACK_BIN_DIRS", ())
    monkeypatch.setattr(
        bridge_common, "_harvest_login_path", lambda timeout=3.0: str(real)
    )
    monkeypatch.setenv("PATH", os.pathsep.join([str(real), "/usr/bin"]))

    added = bridge_common.ensure_user_path()

    assert added == []                        # no duplicate insertion
    assert os.environ["PATH"] == os.pathsep.join([str(real), "/usr/bin"])


def test_falls_back_when_harvest_fails(tmp_path, monkeypatch):
    fallback = tmp_path / "fallbin"
    fallback.mkdir()
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(bridge_common, "_FALLBACK_BIN_DIRS", (str(fallback),))
    monkeypatch.setattr(bridge_common, "_harvest_login_path", lambda timeout=3.0: None)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    added = bridge_common.ensure_user_path()

    assert added == [str(fallback)]           # fallback used when harvest yields nothing
    assert os.environ["PATH"].split(os.pathsep)[0] == str(fallback)


def test_noop_on_non_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    added = bridge_common.ensure_user_path()

    assert added == []
    assert os.environ["PATH"] == "/usr/bin:/bin"


def test_harvest_extracts_path_between_markers(tmp_path, monkeypatch):
    # A fake $SHELL that prints a banner then the marker-fenced PATH; proves
    # _harvest_login_path strips rc-file noise and returns just the value.
    m = bridge_common._PATH_MARKER
    fake = tmp_path / "fakeshell"
    fake.write_text('#!/bin/sh\nprintf %%s "startup-banner%s/X:/Y%s"\n' % (m, m))
    fake.chmod(0o755)
    monkeypatch.setenv("SHELL", str(fake))

    assert bridge_common._harvest_login_path(timeout=5) == "/X:/Y"
