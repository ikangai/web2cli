"""Held-lock turn protocol on _Registry, driven through real tmux + the
canonical fake_claude.sh (Tasks 23-25).

Completion is the FILE edge (rendezvous await_envelope), never screen
quiescence. The fake, on the build_turn_prompt WRITE handshake, writes
env.<uuid>.json.part then renames it; an empty instruction sends nothing.
"""
import json
import os
import time

import pytest

from conftest import requires_tmux   # bare import (import-mode pinned in conftest)

import paths
import session_registry


@pytest.fixture
def live_session(tmp_base, fake_socket, fake_claude_argv, monkeypatch):
    """A real fake-claude session created through the real TmuxClient."""
    monkeypatch.setenv("WCB_FAKE_DELAY", "0.4")
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = session_registry._Registry(base=str(tmp_base))
    sess = reg.create(
        cwd=str(tmp_base), cols=120, rows=40, claude_argv=fake_claude_argv,
        socket_override=fake_socket,
    )
    yield reg, sess
    try:
        reg.delete(sess.sid, sess.cap)
    except Exception:
        pass


# --- Task 23: bounded acquire -> 409, lock released after a turn -------------

@requires_tmux
def test_busy_409_when_turn_lock_held(live_session):
    reg, sess = live_session
    # Simulate an in-flight turn by holding the turn lock.
    assert sess.turn_lock.acquire(timeout=2.0)
    try:
        # A second concurrent turn must NOT be able to acquire within budget.
        got = sess.turn_lock.acquire(timeout=2.0)
        assert got is False, "bounded acquire must give up -> 409 path"
    finally:
        sess.turn_lock.release()


@requires_tmux
def test_session_busy_raised_when_lock_held(live_session):
    reg, sess = live_session
    assert sess.turn_lock.acquire(timeout=2.0)
    try:
        with pytest.raises(session_registry.SessionBusy):
            reg.run_turn(sess, instruction="noop", doc="<html></html>",
                         turn_uuid=reg.mint_turn_uuid(), timeout=3.0)
    finally:
        sess.turn_lock.release()


@requires_tmux
def test_lock_released_after_turn_without_killing_session(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    result = reg.run_turn(
        sess, instruction="WRITE", doc="<html></html>",
        turn_uuid=turn_uuid, timeout=10.0,
    )
    assert result["reason"] in ("idle", "idle_no_envelope")
    # Lock is free again after the turn.
    assert sess.turn_lock.acquire(timeout=2.0) is True
    sess.turn_lock.release()
    # Session is still alive (a turn ending != the session dying).
    assert sess.tmux.has_session("t") is True


# --- Task 24: stage doc (LF-only) + stale-env sweep before send --------------

@requires_tmux
def test_doc_staged_lf_only_before_send(live_session, monkeypatch):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    doc = "<html>\r\n<body>line</body>\r\n</html>"  # caller passes CRLF
    staged_at_send = {}
    orig_send_prompt = sess.tmux.send_prompt

    def spy_send_prompt(target, text):
        doc_path = os.path.join(sess.rendezvous_dir, f"doc.{turn_uuid}.html")
        with open(doc_path, "rb") as f:
            staged_at_send["bytes"] = f.read()
        staged_at_send["prompt"] = text
        return orig_send_prompt(target, text)

    monkeypatch.setattr(sess.tmux, "send_prompt", spy_send_prompt)

    reg.run_turn(sess, instruction="WRITE", doc=doc,
                 turn_uuid=turn_uuid, timeout=10.0)

    # Doc staged BEFORE send (happens-before), LF-only, no CR.
    assert "bytes" in staged_at_send, "doc must be staged before send_prompt"
    assert b"\r" not in staged_at_send["bytes"]
    assert b"<body>line</body>" in staged_at_send["bytes"]
    # Production prompt is build_turn_prompt output naming the env file —
    # NOT a literal '{env} {uuid}' template.
    assert turn_uuid in staged_at_send["prompt"]
    assert "{env}" not in staged_at_send["prompt"]


@requires_tmux
def test_stale_env_unlinked_at_turn_start(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    env_path = os.path.join(sess.rendezvous_dir, f"env.{turn_uuid}.json")
    with open(env_path, "wb") as f:
        f.write(b'{"stale":true}')
    # Empty instruction => fake stays idle (writes nothing).
    result = reg.run_turn(sess, instruction="", doc="<html></html>",
                          turn_uuid=turn_uuid, timeout=8.0)
    # stale-env swept at turn start (by stage_turn) and never recreated =>
    # idle_no_envelope, never a stale read.
    assert result["reason"] == "idle_no_envelope"
    assert not os.path.exists(env_path)


# --- Task 25: file-edge completion + byte-exact, sentinel-checked read-back --

@requires_tmux
def test_file_edge_done_idle_with_byte_exact_envelope(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    # canonical fake_claude.sh, on the build_turn_prompt WRITE handshake,
    # writes env.part then renames it to env.<uuid>.json.
    result = reg.run_turn(
        sess, instruction="WRITE", doc="<html></html>",
        turn_uuid=turn_uuid, timeout=12.0,
    )
    assert result["reason"] == "idle"
    env_bytes = result["envelope_bytes"]
    assert isinstance(env_bytes, (bytes, bytearray))
    obj = json.loads(env_bytes)
    assert obj["turn_uuid"] == turn_uuid          # sentinel/uuid echo (risk #4)
    assert obj["tool"]                             # truthy, per read-back


@requires_tmux
def test_no_write_yields_idle_no_envelope_never_stale(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    result = reg.run_turn(
        sess, instruction="", doc="<html></html>", turn_uuid=turn_uuid,
        timeout=8.0,
    )
    assert result["reason"] == "idle_no_envelope"
    assert result.get("envelope_bytes") is None


@requires_tmux
def test_mtime_must_exceed_send_time(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    env_path = os.path.join(sess.rendezvous_dir, f"env.{turn_uuid}.json")
    # Plant an old-content file (swept at turn start by stage_turn); then a
    # real WRITE. The poll accepts ONLY the fresh post-send file.
    with open(env_path, "wb") as f:
        f.write(b'{"tool":"x","envelope":{"a":1},"turn_uuid":"%s","gen":"%s"}'
                % (turn_uuid.encode(), turn_uuid.encode()))
    old = time.time() - 3600
    os.utime(env_path, (old, old))
    result = reg.run_turn(
        sess, instruction="WRITE", doc="<html></html>",
        turn_uuid=turn_uuid, timeout=12.0,
    )
    assert result["reason"] == "idle"
    assert json.loads(result["envelope_bytes"])["turn_uuid"] == turn_uuid
