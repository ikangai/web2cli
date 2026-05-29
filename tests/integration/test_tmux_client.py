# tests/integration/test_tmux_client.py
import os
import time

import pytest

from conftest import TMUX, FAKE, requires_tmux
from tmux_session import TmuxClient, _TmuxError

pytestmark = requires_tmux

FAKE_ARGV = ["bash", str(FAKE)]
IDLE = "bypass permissions on (shift+tab to cycle)"


def _wait_for(c, target, needle, timeout=10.0):
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = c.capture_pane(target)
        if needle in last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"never saw {needle!r}; last screen:\n{last}")


def test_create_has_kill(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    pane = c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    assert pane.startswith("%")
    assert c.has_session("t") is True
    assert c.pane_id("t") == pane
    c.kill_server()
    assert c.has_session("t") is False


def test_socket_path_reports_l_socket(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    sp = c.socket_path()
    # The -L socket is a real path on disk while the server is up.
    assert isinstance(sp, str) and sp != ""
    assert fake_socket in sp
    assert os.path.exists(sp)


def test_kill_server_then_recreate_same_socket(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    c.kill_server()
    # A fresh server on the same socket name must come up cleanly (risk #11).
    c2 = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c2.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    assert c2.has_session("t") is True


def test_has_session_false_for_unknown_socket():
    c = TmuxClient(socket="wcbtest_nonexistent_zzzz", tmux_bin=TMUX)
    # No server running on this socket -> gone, NOT an exception.
    assert c.has_session("t") is False


def test_tmux_error_classification_authoritative(fake_socket):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    # No server yet -> pane_id on a missing session is authoritative (not retryable).
    with pytest.raises(_TmuxError) as ei:
        c.pane_id("t")
    assert ei.value.retryable is False


def test_tmux_binary_missing_is_authoritative_classified(tmp_path):
    c = TmuxClient(socket="wcbtest_x", tmux_bin="/nonexistent/tmux_binary_xyz")
    with pytest.raises(_TmuxError) as ei:
        c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=["true"])
    # A missing tmux binary is authoritative (-> 503 upstream), surfaced as a
    # non-retryable _TmuxError carrying the cause.
    assert ei.value.retryable is False
    assert "tmux" in str(ei.value).lower()


def test_capture_pane_returns_composer(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    screen = _wait_for(c, "t", IDLE)
    assert "❯ " in screen


def test_alternate_on_is_zero(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    # The whole inline-capture model depends on alternate_on == 0.
    assert c.alternate_on("t") == 0


def test_pane_current_command(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    cmd = c.pane_current_command("t")
    # The fake runs under bash; the real binary reports 'claude.exe'. Either
    # way it is a non-empty command name (DEAD discriminator is shell-relative).
    assert isinstance(cmd, str) and cmd != ""


def test_pane_dead_false_while_running(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    assert c.pane_dead("t") is False


def test_tpgid_positive_and_is_a_pgid(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    pg = c.tpgid("t")
    assert pg > 0
    # tpgid is a real process-group id (os.getpgid of the pane pid), so the
    # guarded killpg in Task 9 targets the group, not a lone pid.
    assert os.getpgid(os.getpgid(pg) and pg or pg) == pg  # pg is its own group's id


def test_user_option_roundtrip(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.set_option("t", "@wcb_nonce", "abc123")
    assert c.get_option("t", "@wcb_nonce") == "abc123"


def test_missing_option_is_default_not_dead(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    # An unset @wcb_* option is a recoverable default (None), NOT a death signal.
    assert c.get_option("t", "@wcb_never_set") is None
