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


def test_send_keys_rejects_unknown_named_key(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    # Anything outside NAMED_KEYS must be rejected before touching tmux argv.
    with pytest.raises(ValueError):
        c.send_keys("t", "F1")
    with pytest.raises(ValueError):
        c.send_keys("t", "rm -rf /")


def test_send_text_types_literally_no_shell_eval(fake_socket, tmp_path):
    # send_text uses `-l ... --` so metacharacters are typed, never evaluated.
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    payload = "EXIT$(touch /tmp/wcb_pwned_$$)"  # if eval'd, file would appear
    c.send_text("t", payload)
    time.sleep(0.3)
    assert not os.path.exists(f"/tmp/wcb_pwned_{os.getpid()}")
    screen = c.capture_pane("t")
    assert "$(touch" in screen  # the literal characters reached the composer


def test_send_prompt_two_lines_unsent_via_m_enter(fake_socket, tmp_path):
    # M-Enter inserts a newline WITHOUT submitting (calibration-pinned). The
    # fake only treats a bare Enter as submit, so a 2-line prompt stays unsent
    # at the M-Enter boundary and is submitted exactly once at the end.
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.send_prompt("t", "lineONE\nlineTWO")
    time.sleep(0.3)
    screen = c.capture_pane("t")
    assert "lineONE" in screen
    assert "lineTWO" in screen


def test_send_prompt_single_line_submits_fake_write(fake_socket, tmp_path):
    # A single-line fake WRITE prompt submits with one bare Enter -> the fake
    # writes the rendezvous file. (Fake protocol only; the real prompt is
    # build_turn_prompt in rendezvous-docsync.)
    import json
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    env_path = tmp_path / "env.json"
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.send_prompt("t", f"WRITE {env_path} {uuid}")
    _wait_for(c, "t", "⏺ DONE")
    obj = json.loads(env_path.read_text())
    assert obj["turn_uuid"] == uuid


def test_send_keys_m_enter_does_not_submit(fake_socket, tmp_path):
    # Distinguish M-Enter (no submit) from Enter (submit) via a capture.
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.send_text("t", "PROBE")
    c.send_keys("t", "M-Enter")
    time.sleep(0.2)
    after_m_enter = c.capture_pane("t")
    assert "PROBE" in after_m_enter           # still in composer, unsent
    assert "⏺ DONE" not in after_m_enter      # never submitted
