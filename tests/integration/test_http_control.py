"""/session/{capture,send-key,replay,interrupt,delete} over real HTTP.

Sessions are created directly on the server's registry with the fake claude (the
HTTP /session/create endpoint always launches REAL claude — risk #6), then
driven over HTTP. Each cap-bound route is also checked to reject a wrong cap.
"""
import base64

import pytest

from conftest import requires_tmux


def _mk(session_http, fake_socket, fake_claude_argv):
    return session_http.reg.create(
        cwd=session_http.base, cols=120, rows=40,
        claude_argv=fake_claude_argv, socket_override=fake_socket,
    )


@requires_tmux
def test_capture_returns_screen_state_offset(session_http, fake_socket,
                                             fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/capture", {"session_id": sess.sid, "cap": sess.cap})
        assert status == 200
        assert isinstance(body["screen"], str)
        assert body["state"] in ("idle", "idle_no_envelope", "starting",
                                 "awaiting_input")
        assert isinstance(body["log_offset"], int)
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_capture_wrong_cap_is_403(session_http, fake_socket, fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/capture", {"session_id": sess.sid, "cap": "0" * 64})
        assert status == 403
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_send_key_named_then_literal_text_appears(session_http, fake_socket,
                                                  fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/send-key",
            {"session_id": sess.sid, "cap": sess.cap, "keys": ["Enter"]})
        assert status == 200 and body["ok"] is True
        status, body, _ = session_http.post(
            "/session/send-key",
            {"session_id": sess.sid, "cap": sess.cap, "keys": ["hello world"]})
        assert status == 200
        _, cap_body, _ = session_http.post(
            "/session/capture", {"session_id": sess.sid, "cap": sess.cap})
        assert "hello world" in cap_body["screen"]
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_send_key_rejects_non_string_element_400(session_http, fake_socket,
                                                 fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/send-key",
            {"session_id": sess.sid, "cap": sess.cap, "keys": [123]})
        assert status == 400
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_send_key_rejects_empty_keys_400(session_http, fake_socket,
                                         fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/send-key",
            {"session_id": sess.sid, "cap": sess.cap, "keys": []})
        assert status == 400
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_replay_closed_slice_is_monotonic(session_http, fake_socket,
                                          fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        with open(sess.log_path, "ab") as f:
            f.write(b"MARKER-ABCDEFGHIJ")
        status, body, _ = session_http.post(
            "/session/replay",
            {"session_id": sess.sid, "cap": sess.cap, "from_offset": 0})
        assert status == 200
        data = base64.b64decode(body["bytes"])
        assert b"MARKER-ABCDEFGHIJ" in data
        assert body["from_offset"] == 0
        assert body["end_offset"] == len(data)
        end = body["end_offset"]
        # Closed slice: bytes appended AFTER the snapshot are not in [0,end).
        with open(sess.log_path, "ab") as f:
            f.write(b"TAIL-ZZZZ")
        status, body2, _ = session_http.post(
            "/session/replay",
            {"session_id": sess.sid, "cap": sess.cap, "from_offset": end})
        assert status == 200
        tail = base64.b64decode(body2["bytes"])
        assert b"TAIL-ZZZZ" in tail
        assert b"MARKER-ABCDEFGHIJ" not in tail
        assert body2["from_offset"] == end
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_replay_rejects_negative_offset_400(session_http, fake_socket,
                                            fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/replay",
            {"session_id": sess.sid, "cap": sess.cap, "from_offset": -1})
        assert status == 400
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_interrupt_ok_and_keeps_session(session_http, fake_socket,
                                        fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/interrupt", {"session_id": sess.sid, "cap": sess.cap})
        assert status == 200 and body == {"ok": True}
        # interrupt must never tear the session down.
        assert sess.tmux.has_session("t") is True
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_interrupt_bad_cap_403(session_http, fake_socket, fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/interrupt", {"session_id": sess.sid, "cap": "0" * 64})
        assert status == 403
        assert sess.tmux.has_session("t") is True
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_delete_over_http_tears_down(session_http, fake_socket,
                                     fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    deleted = False
    try:
        status, body, _ = session_http.post(
            "/session/delete", {"session_id": sess.sid, "cap": sess.cap})
        assert status == 200 and body == {"ok": True}
        assert sess.tmux.has_session("t") is False
        deleted = True
    finally:
        if not deleted:
            sess.tmux.kill_server()


@requires_tmux
def test_delete_bad_cap_403_keeps_session(session_http, fake_socket,
                                          fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/delete", {"session_id": sess.sid, "cap": "0" * 64})
        assert status == 403
        assert sess.tmux.has_session("t") is True
    finally:
        sess.tmux.kill_server()
