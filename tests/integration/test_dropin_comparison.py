"""DROP-IN COMPARISON: `claude -p` (legacy POST /run) vs the tmux session path
(POST /session/create -> /session/stream -> /session/get-envelope).

For each scenario the SAME `rwa-edit/1` envelope is produced by both paths; the
test asserts the rwa's parseBridgeEnvelope extracts an equivalent object from
each, proving the bridge is a faithful byte pipe either way. It also documents
the one real difference (chatty /run stdout vs clean get-envelope bytes) and the
capability the session path ADDS (multi-turn continuity) while keeping the
envelope contract identical.

Uses tests/fake_claude_dropin.sh so the comparison is deterministic in CI; the
live A/B against real claude is in tests/live/test_dropin_live.py (opt-in).
"""
import base64
import json

import pytest

from conftest import DROPIN_FAKE, requires_tmux
from bridge_envelope import parse_bridge_envelope
from test_http_stream import _parse_sse

SCENARIOS = ["apply_edits", "replace_document", "apply_dsl_plan"]

# The exact instruction the rwa would send; the session path stages this doc and
# the fake's TUI write-handshake produces the scenario envelope.
DOC = "<html><h1>Old Title</h1></html>"


def _session_turn(client, sess, instruction="WRITE", doc=DOC, timeout=12.0):
    status, raw, _ = client.post(
        "/session/stream",
        {"session_id": sess.sid, "cap": sess.cap, "doc": doc,
         "instruction": instruction, "timeout": timeout}, raw=True)
    assert status == 200, raw
    done = [d for n, d in _parse_sse(raw.decode()) if n == "done"][-1]
    return done["turn_uuid"], done["reason"]


def _claude_p_via_run(client, scenario):
    """Mirror the rwa's `echo <b64> | base64 -d | claude -p` pipeline."""
    prompt = "Apply scenario %s to the document." % scenario
    b64 = base64.b64encode(prompt.encode()).decode()
    cmd = "printf %%s '%s' | base64 -d | bash %s -p" % (b64, DROPIN_FAKE)
    status, body, _ = client.post("/run", {"command": cmd})
    assert status == 200, body
    return body["stdout"]


def _new_session(client, fake_socket):
    return client.reg.create(
        cwd=client.base, cols=120, rows=40,
        claude_argv=["bash", str(DROPIN_FAKE)], socket_override=fake_socket)


@requires_tmux
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_envelope_identical_between_claude_p_and_session(
        scenario, session_http, fake_socket, monkeypatch):
    monkeypatch.setenv("WCB_DROPIN_SCENARIO", scenario)

    # OLD path — legacy /run + `claude -p` analog.
    old_stdout = _claude_p_via_run(session_http, scenario)
    old = parse_bridge_envelope(old_stdout)

    # NEW path — persistent session create -> stream -> get-envelope.
    sess = _new_session(session_http, fake_socket)
    try:
        turn_uuid, reason = _session_turn(session_http, sess)
        assert reason == "idle"
        _, raw, headers = session_http.post(
            "/session/get-envelope",
            {"session_id": sess.sid, "cap": sess.cap, "turn_uuid": turn_uuid},
            raw=True)
        new = parse_bridge_envelope(raw)   # rwa runs the SAME parser on these

        # DROP-IN CONTRACT: identical edit operation + identical rwa-edit/1
        # payload from both paths.
        assert old["tool"] == new["tool"] == scenario
        assert old["envelope"] == new["envelope"]
        assert new["envelope"]["version"] == "rwa-edit/1"
        assert headers.get("Content-Type") == "application/json"
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_run_stdout_is_chatty_but_get_envelope_is_clean_json(
        session_http, fake_socket, monkeypatch):
    """The one real difference, and why it's still a drop-in: /run returns the
    envelope embedded in prose (parseBridgeEnvelope must extract it); get-
    envelope returns clean JSON bytes. Both yield the same object."""
    monkeypatch.setenv("WCB_DROPIN_SCENARIO", "apply_edits")
    old_stdout = _claude_p_via_run(session_http, "apply_edits")
    assert "Here is the edit envelope" in old_stdout          # prose present
    old = parse_bridge_envelope(old_stdout)                   # extractor copes

    sess = _new_session(session_http, fake_socket)
    try:
        turn_uuid, _ = _session_turn(session_http, sess)
        _, raw, _ = session_http.post(
            "/session/get-envelope",
            {"session_id": sess.sid, "cap": sess.cap, "turn_uuid": turn_uuid},
            raw=True)
        direct = json.loads(raw)                              # parses directly
        assert direct["tool"] == old["tool"] == "apply_edits"
        assert direct["envelope"] == old["envelope"]
    finally:
        sess.tmux.kill_server()


@requires_tmux
def test_session_persists_across_turns_with_unchanged_contract(
        session_http, fake_socket, monkeypatch):
    """The capability the session path ADDS over `claude -p`: two turns on ONE
    session, each with a fresh bridge-minted turn_uuid and a valid envelope —
    while the envelope contract is unchanged. The legacy path needs a fresh
    claude per call and keeps no continuity.

    Protocol note (a real drop-in detail): each turn's envelope is ephemeral —
    stage_turn sweeps stale env.*.json at the next turn's start — so the caller
    fetches get-envelope BETWEEN turns (stream -> get-envelope -> next stream),
    exactly as the rwa does."""
    monkeypatch.setenv("WCB_DROPIN_SCENARIO", "apply_edits")
    sess = _new_session(session_http, fake_socket)

    def _fetch(u):
        _, raw, _ = session_http.post(
            "/session/get-envelope",
            {"session_id": sess.sid, "cap": sess.cap, "turn_uuid": u}, raw=True)
        return parse_bridge_envelope(raw)

    try:
        u1, r1 = _session_turn(session_http, sess)
        env1 = _fetch(u1)                         # fetch BEFORE the next turn
        u2, r2 = _session_turn(session_http, sess)
        env2 = _fetch(u2)
        assert r1 == r2 == "idle"
        assert u1 != u2, "each turn gets a distinct bridge-minted uuid"
        assert env1["tool"] == env2["tool"] == "apply_edits"
        assert env1["turn_uuid"] == u1 and env2["turn_uuid"] == u2
        assert sess.tmux.has_session("t") is True   # one session served both
    finally:
        sess.tmux.kill_server()
