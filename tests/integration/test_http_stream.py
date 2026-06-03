"""/session/stream (SSE held-lock turn) over real HTTP + the fake claude.

One turn is a single critical section: stage doc -> send prompt -> file-edge
completion -> envelope read, the lock held throughout and released in finally
WITHOUT killing the session. The completion edge is the FILE, surfaced as a
`done` event whose `reason` is idle / idle_no_envelope.
"""
import json

import pytest

import session_endpoints as se
from conftest import requires_tmux


def _mk(session_http, fake_socket, fake_claude_argv):
    return session_http.reg.create(
        cwd=session_http.base, cols=120, rows=40,
        claude_argv=fake_claude_argv, socket_override=fake_socket,
    )


def _parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        ev = data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if ev is not None:
            events.append((ev, json.loads(data) if data else None))
    return events


@requires_tmux
def test_stream_done_idle_with_envelope(session_http, fake_socket,
                                        fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, raw, headers = session_http.post(
            "/session/stream",
            {"session_id": sess.sid, "cap": sess.cap, "doc": "<html></html>",
             "instruction": "WRITE", "timeout": 12.0}, raw=True)
        assert status == 200
        assert headers.get("Content-Type") == "text/event-stream"
        events = _parse_sse(raw.decode())
        names = [n for n, _ in events]
        assert "done" in names
        done = [d for n, d in events if n == "done"][-1]
        assert done["reason"] == "idle"
        assert done["alive"] is True
        assert "turn_uuid" in done
        # a turn ending must NOT kill the session
        assert sess.tmux.has_session("t") is True
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_stream_empty_instruction_idle_no_envelope(session_http, fake_socket,
                                                   fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, raw, _ = session_http.post(
            "/session/stream",
            {"session_id": sess.sid, "cap": sess.cap, "doc": "<html></html>",
             "instruction": "", "timeout": 8.0}, raw=True)
        assert status == 200
        done = [d for n, d in _parse_sse(raw.decode()) if n == "done"][-1]
        assert done["reason"] == "idle_no_envelope"
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_stream_doc_not_staged_409(session_http, fake_socket, fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/stream",
            {"session_id": sess.sid, "cap": sess.cap, "instruction": "x"})
        assert status == 409
        assert body["error"] == "doc_not_staged"
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_stream_busy_409_when_lock_held(session_http, fake_socket,
                                        fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        assert sess.turn_lock.acquire(timeout=2.0)
        try:
            status, body, _ = session_http.post(
                "/session/stream",
                {"session_id": sess.sid, "cap": sess.cap,
                 "doc": "<html></html>", "instruction": "WRITE",
                 "timeout": 8.0})
            assert status == 409
            assert body["error"] == "session busy"
        finally:
            sess.turn_lock.release()
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_stream_turn_error_after_commit_releases_lock_and_emits_done_error(
        session_http, fake_socket, fake_claude_argv, monkeypatch):
    """A turn failure AFTER the SSE 200 is committed (e.g. tmux dies mid-turn)
    must NOT (a) leak the turn lock — wedging the session at 409 forever — nor
    (b) write a second HTTP response into the already-committed event stream. It
    degrades to a single `done` event with reason=error, lock released."""
    sess = session_http.reg.create(
        cwd=session_http.base, cols=120, rows=40,
        claude_argv=fake_claude_argv, socket_override=fake_socket)
    try:
        def boom(*a, **k):
            raise se._TmuxError("server died mid-turn", retryable=True)

        monkeypatch.setattr(session_http.reg, "run_turn_locked", boom)
        status, raw, _ = session_http.post(
            "/session/stream",
            {"session_id": sess.sid, "cap": sess.cap, "doc": "<html></html>",
             "instruction": "WRITE", "timeout": 8.0}, raw=True)
        assert status == 200                      # already committed
        events = _parse_sse(raw.decode())
        names = [n for n, _ in events]
        assert names.count("done") == 1, "exactly one done event, no 2nd response"
        done = [d for n, d in events if n == "done"][0]
        assert done["reason"] == "error"
        # CRUCIAL: the lock was released — the session is not wedged.
        assert sess.turn_lock.acquire(timeout=2.0) is True
        sess.turn_lock.release()
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_stream_wrong_cap_403_before_stream(session_http, fake_socket,
                                            fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/stream",
            {"session_id": sess.sid, "cap": "0" * 64, "doc": "<html></html>",
             "instruction": "WRITE"})
        assert status == 403
    finally:
        sess.tmux.kill_server()
