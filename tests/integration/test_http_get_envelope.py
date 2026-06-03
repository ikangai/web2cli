"""/session/get-envelope over real HTTP — byte-exact retrieval of claude's
envelope (no re-serialization, no second LLM) + the 400/403/404 ladder.

This closes the drop-in loop: after a stream turn writes the rendezvous file,
the rwa fetches the ORIGINAL bytes here and runs its own parseBridgeEnvelope on
exactly what claude produced.
"""
import json

import pytest

from conftest import requires_tmux

from test_http_stream import _mk, _parse_sse


def _run_turn(session_http, sess, instruction="WRITE"):
    status, raw, _ = session_http.post(
        "/session/stream",
        {"session_id": sess.sid, "cap": sess.cap, "doc": "<html></html>",
         "instruction": instruction, "timeout": 12.0}, raw=True)
    assert status == 200
    done = [d for n, d in _parse_sse(raw.decode()) if n == "done"][-1]
    return done["turn_uuid"], done["reason"]


@requires_tmux
def test_get_envelope_returns_original_bytes_verbatim(session_http, fake_socket,
                                                      fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        turn_uuid, reason = _run_turn(session_http, sess)
        assert reason == "idle"
        status, raw, headers = session_http.post(
            "/session/get-envelope",
            {"session_id": sess.sid, "cap": sess.cap, "turn_uuid": turn_uuid},
            raw=True)
        assert status == 200
        assert headers.get("Content-Type") == "application/json"
        # byte-exact: identical to what is on disk (no re-serialization).
        import os
        env_file = os.path.join(sess.rendezvous_dir,
                                "env.%s.json" % turn_uuid)
        with open(env_file, "rb") as f:
            assert raw == f.read()
        obj = json.loads(raw)
        assert obj["turn_uuid"] == turn_uuid
        assert isinstance(obj["tool"], str) and obj["envelope"]
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_get_envelope_missing_is_404(session_http, fake_socket,
                                     fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/get-envelope",
            {"session_id": sess.sid, "cap": sess.cap,
             "turn_uuid": "12345678-1234-1234-1234-1234567890ab"})
        assert status == 404
        assert body["error"] == "envelope_not_written"
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_get_envelope_bad_turn_uuid_is_400(session_http, fake_socket,
                                           fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        status, body, _ = session_http.post(
            "/session/get-envelope",
            {"session_id": sess.sid, "cap": sess.cap,
             "turn_uuid": "../etc/passwd"})
        assert status == 400
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_get_envelope_wrong_cap_is_403(session_http, fake_socket,
                                       fake_claude_argv):
    sess = _mk(session_http, fake_socket, fake_claude_argv)
    try:
        turn_uuid, _ = _run_turn(session_http, sess)
        status, body, _ = session_http.post(
            "/session/get-envelope",
            {"session_id": sess.sid, "cap": "0" * 64, "turn_uuid": turn_uuid})
        assert status == 403
    finally:
        sess.tmux.kill_server()
