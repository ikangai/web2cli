"""End-to-end wiring: SessionMixin mounted on server.Handler.

Confirms the dispatcher is reachable over real HTTP (token + origin gates, CORS
never `*`, OPTIONS preflight) and that the legacy /run path is untouched. None of
these launch claude — they exercise list/create-validation/auth only.
"""


def test_session_list_requires_token(session_http):
    status, body, _ = session_http.get("/session/list", token=None)
    assert status == 401


def test_session_list_ok_with_auth(session_http):
    status, body, _ = session_http.get("/session/list")
    assert status == 200
    assert body["sessions"] == []


def test_session_bad_origin_is_403(session_http):
    status, body, _ = session_http.get("/session/list",
                                       origin="https://evil.example")
    assert status == 403


def test_session_cors_never_star(session_http):
    status, body, headers = session_http.get("/session/list")
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == session_http.origin
    assert headers.get("Access-Control-Allow-Origin") != "*"


def test_options_preflight_reflects_allowed_origin(session_http):
    status, body, headers = session_http.request("OPTIONS", "/session/list")
    assert status == 204
    assert headers.get("Access-Control-Allow-Origin") == session_http.origin
    assert headers.get("Access-Control-Allow-Origin") != "*"


def test_create_missing_cwd_is_400_over_http(session_http):
    # Reaches _do_create's validation BEFORE any claude launch.
    status, body, _ = session_http.post("/session/create", {})
    assert status == 400
    assert "cwd" in body["error"]


def test_create_requires_token(session_http):
    status, body, _ = session_http.post("/session/create",
                                        {"cwd": "/tmp"}, token=None)
    assert status == 401


def test_legacy_run_path_untouched(session_http):
    # /run still works (with the now-mandatory token) — no session regression.
    status, body, _ = session_http.post("/run", {"command": "echo wired"})
    assert status == 200
    assert body["stdout"].strip() == "wired"
    assert body["exit_code"] == 0


def test_unknown_get_route_405(session_http):
    status, body, _ = session_http.get("/session/capture")
    assert status == 405
