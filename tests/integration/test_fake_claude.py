"""Drive fake_claude.sh raw through the real tmux binary and assert the
load-bearing edges: composer-ready, the trust gate, the WRITE -> .part ->
rename -> DONE completion edge, the rising-spinner WORKING timer,
the SIGINT trap marker, and the EXIT -> claude --resume + shell DEAD edge.

This is the ONE canonical integration harness; every later component imports
fake_claude.sh from here via conftest.FAKE and drives it the same way.
"""
import json
import os
import pathlib
import subprocess
import time

import pytest

from conftest import TMUX, FAKE, requires_tmux


def _tmux(sock, *args, **kw):
    return subprocess.run(
        [TMUX, "-L", sock, *args],
        capture_output=True, text=True, **kw,
    )


def _capture(sock):
    return _tmux(sock, "capture-pane", "-p", "-t", "t").stdout


def _wait_for(sock, needle, timeout=10.0, want=True):
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = _capture(sock)
        if (needle in last) == want:
            return last
        time.sleep(0.1)
    raise AssertionError(
        f"timeout waiting for {needle!r} present={want}; last screen:\n{last}"
    )


@requires_tmux
def test_fake_is_executable_and_present():
    assert FAKE.is_file(), f"missing {FAKE}"
    assert os.access(FAKE, os.X_OK), "fake_claude.sh must be executable"


@requires_tmux
def test_composer_ready_footer(fake_socket, tmp_path):
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "bash", str(FAKE))
    screen = _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")
    assert "❯" in screen
    assert "esc to interrupt" not in screen  # idle, not working
    assert "← for agents" in screen          # canonical IDLE suffix


@requires_tmux
def test_trust_gate_then_composer(fake_socket, tmp_path):
    env = "WCB_FAKE_TRUST=1"
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "env", env, "bash", str(FAKE))
    _wait_for(fake_socket, "Is this a project you created or one you trust?")
    screen = _capture(fake_socket)
    assert "❯ 1. Yes, I trust this folder" in screen
    # Answer the trust prompt the way create() will: ["1","Enter"].
    _tmux(fake_socket, "send-keys", "-t", "t", "-l", "--", "1")
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")


@requires_tmux
def test_write_part_rename_done_edge(fake_socket, tmp_path):
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "env", "WCB_FAKE_DELAY=0.3", "bash", str(FAKE))
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")

    env_path = tmp_path / "env.test.json"
    turn_uuid = "8411fd46-093b-4fdd-9ae0-183cfa5ba98b"
    assert not env_path.exists()

    send_time = time.time()
    line = f"WRITE {env_path} {turn_uuid}"
    _tmux(fake_socket, "send-keys", "-t", "t", "-l", "--", line)
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")

    # Footer flips to WORKING during the delay.
    _wait_for(fake_socket, "esc to interrupt")

    # The FINAL renamed file appears (never a lingering .part).
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if env_path.exists():
            break
        time.sleep(0.05)
    assert env_path.exists(), "renamed env file never appeared"
    assert not (tmp_path / "env.test.json.part").exists(), ".part was not renamed"

    data = json.loads(env_path.read_text())
    assert data["turn_uuid"] == turn_uuid
    assert data["tool"] == "echo"
    assert data["envelope"]                       # truthy, per read-back contract
    assert env_path.stat().st_mtime >= send_time - 1  # mtime after send

    _wait_for(fake_socket, "⏺ DONE")
    # Footer reverts to IDLE after the turn.
    _wait_for(fake_socket, "esc to interrupt", want=False)


@requires_tmux
def test_rising_spinner_timer_mode(fake_socket, tmp_path):
    # Risk #2 end-to-end: WORKING is also signalled by a rising spinner timer.
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "env", "WCB_FAKE_TIMER=1", "WCB_FAKE_DELAY=0.3",
          "bash", str(FAKE))
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")
    env_path = tmp_path / "env.timer.json"
    line = f"WRITE {env_path} 11111111-2222-3333-4444-555555555555"
    _tmux(fake_socket, "send-keys", "-t", "t", "-l", "--", line)
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")
    # The elapsed seconds rise across captures: (1s ...) then a higher value.
    _wait_for(fake_socket, "Smooshing… (1s")
    _wait_for(fake_socket, "Smooshing… (2s")
    _wait_for(fake_socket, "⏺ DONE")


@requires_tmux
def test_sigint_trap_prints_interrupted(fake_socket, tmp_path):
    # cleanup-security's interrupt path relies on this trap marker.
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "bash", str(FAKE))
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")
    _tmux(fake_socket, "send-keys", "-t", "t", "C-c")
    _wait_for(fake_socket, "^C INTERRUPTED")


@requires_tmux
def test_blank_line_is_noop_and_exit_quits(fake_socket, tmp_path):
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "bash", str(FAKE))
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")
    # Blank line: still idle, no WORKING footer.
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")
    time.sleep(0.3)
    assert "esc to interrupt" not in _capture(fake_socket)
    # EXIT quits the mimic after printing the resume hint; session ends.
    _tmux(fake_socket, "send-keys", "-t", "t", "-l", "--", "EXIT")
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")
    _wait_for(fake_socket, "claude --resume")
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        rc = _tmux(fake_socket, "has-session", "-t", "t").returncode
        if rc != 0:
            break
        time.sleep(0.1)
    assert _tmux(fake_socket, "has-session", "-t", "t").returncode != 0
