"""LIVE drop-in A/B against REAL claude — opt-in, skipped by default.

    WCB_DROPIN_LIVE=1 python3 -m pytest tests/live/test_dropin_live.py -q

Runs the SAME edit request through both real paths and asserts each yields a
parseable `rwa-edit/1` envelope:

  - legacy:  POST /run  ->  `claude -p --output-format text` (envelope on stdout)
  - session: POST /session/create -> /session/stream -> /session/get-envelope
             (envelope written to the rendezvous file via build_turn_prompt)

Real claude is non-deterministic, so the assertions are STRUCTURAL (valid
envelope of a known tool), not byte-equality — that is the honest live fidelity
bar. The deterministic byte-for-byte comparison lives in
tests/integration/test_dropin_comparison.py.
"""
import os
import shutil

import pytest

from conftest import requires_tmux
from bridge_envelope import parse_bridge_envelope

pytestmark = [
    requires_tmux,
    pytest.mark.skipif(
        os.environ.get("WCB_DROPIN_LIVE") != "1",
        reason="live drop-in A/B opt-in: set WCB_DROPIN_LIVE=1 (needs real claude)",
    ),
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude binary not found"),
]

VALID_TOOLS = {"apply_edits", "replace_document", "apply_dsl_plan"}
DOC = "<html><h1>Old Title</h1></html>"

ONESHOT_PROMPT = (
    "You are editing an HTML document. Output ONLY a single JSON object and no "
    "other text. It MUST be of the form "
    '{"tool":"apply_edits","envelope":{"version":"rwa-edit/1","edits":'
    '[{"find":"<h1>Old Title</h1>","replace":"<h1>New Title</h1>"}]}}. '
    "Change the title from 'Old Title' to 'New Title' in this document:\n" + DOC
)


def _parse_sse(text):
    import json
    out = []
    for block in text.split("\n\n"):
        ev = data = None
        for line in block.strip("\n").split("\n"):
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if ev is not None:
            out.append((ev, json.loads(data) if data else None))
    return out


def test_legacy_claude_p_emits_parseable_envelope(session_http):
    cmd = "claude -p --output-format text --permission-mode bypassPermissions"
    status, body, _ = session_http.post(
        "/run", {"command": cmd, "stdin": ONESHOT_PROMPT, "timeout": 120})
    assert status == 200, body
    env = parse_bridge_envelope(body["stdout"])
    assert env["tool"] in VALID_TOOLS
    assert env["envelope"]["version"] == "rwa-edit/1"


def test_session_path_emits_parseable_envelope(session_http):
    # Drive the WHOLE new path over HTTP with the REAL claude launch line.
    status, created, _ = session_http.post(
        "/session/create", {"cwd": session_http.base, "cols": 120, "rows": 40})
    assert status == 200, created
    sid, cap = created["session_id"], created["cap"]
    try:
        status, raw, _ = session_http.post(
            "/session/stream",
            {"session_id": sid, "cap": cap, "doc": DOC,
             "instruction": "Change the <h1> title from 'Old Title' to "
                            "'New Title'.", "timeout": 120.0}, raw=True)
        assert status == 200
        done = [d for n, d in _parse_sse(raw.decode()) if n == "done"][-1]
        assert done["reason"] == "idle", done
        _, env_raw, headers = session_http.post(
            "/session/get-envelope",
            {"session_id": sid, "cap": cap, "turn_uuid": done["turn_uuid"]},
            raw=True)
        assert headers.get("Content-Type") == "application/json"
        env = parse_bridge_envelope(env_raw)
        assert env["tool"] in VALID_TOOLS
        assert env["envelope"]["version"] == "rwa-edit/1"
        assert env["turn_uuid"] == done["turn_uuid"]
    finally:
        session_http.post("/session/delete", {"session_id": sid, "cap": cap})
