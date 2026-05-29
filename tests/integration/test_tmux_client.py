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
