"""Cleanup-security dispatcher: routing + mandatory token + origin allowlist +
CORS (never `*`) + the exception->HTTP error ladder (design §2, §4, risk #1).

Driven through a lightweight probe that stubs only the BaseHTTPRequestHandler
wire methods the dispatcher touches, so these stay pure unit tests (no socket,
no tmux).
"""
import io
import json

import pytest

import paths
import session_endpoints as se
import session_registry as sr

TOKEN = "s3cr3t-token"
ORIGIN = "http://localhost:5173"


class _Headers:
    """Case-insensitive header lookup like email.message.Message."""
    def __init__(self, d):
        self._d = {k.lower(): v for k, v in d.items()}

    def get(self, k, default=None):
        return self._d.get(k.lower(), default)


class _Probe(se.SessionMixin):
    def __init__(self, path, *, headers=None, body=b""):
        self.path = path
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        hdrs = dict(headers or {})
        if "Content-Length" not in hdrs and body:
            hdrs["Content-Length"] = str(len(body))
        self.headers = _Headers(hdrs)
        self.status = None
        self.sent_headers = {}
        self._ended = False

    # --- stubbed wire surface ---
    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.sent_headers[k] = v

    def end_headers(self):
        self._ended = True


def _auth(token=TOKEN, origin=ORIGIN):
    h = {}
    if token is not None:
        h["Authorization"] = "Bearer " + token
    if origin is not None:
        h["Origin"] = origin
    return h


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WEB_CLI_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setenv("WCB_ALLOWED_ORIGINS", ORIGIN)
    monkeypatch.delenv("WCB_RWA_ORIGIN", raising=False)


# --- auth + origin gates -----------------------------------------------------

def test_missing_token_env_rejects_all_session_calls(monkeypatch):
    # MANDATORY token: with no token configured, every /session/* is 401.
    monkeypatch.delenv("WEB_CLI_BRIDGE_TOKEN", raising=False)
    p = _Probe("/session/list", headers=_auth(token=None))
    p._dispatch_session("GET")
    assert p.status == 401


def test_missing_authorization_header_is_401():
    p = _Probe("/session/list", headers=_auth(token=None))
    p._dispatch_session("GET")
    assert p.status == 401
    assert "WWW-Authenticate" in p.sent_headers


def test_wrong_token_is_401():
    p = _Probe("/session/list", headers=_auth(token="nope"))
    p._dispatch_session("GET")
    assert p.status == 401


def test_non_ascii_bearer_is_401_not_500():
    # compare_digest raises TypeError on non-ASCII str; must degrade to 401.
    p = _Probe("/session/list", headers={"Authorization": "Bearer ÿÿ",
                                         "Origin": ORIGIN})
    p._dispatch_session("GET")
    assert p.status == 401


def test_bad_origin_is_403_before_auth(monkeypatch):
    # Origin gate runs FIRST (before any work). Even a VALID token is rejected
    # when the Origin is not allowlisted (drive-by / DNS-rebind defense).
    p = _Probe("/session/list", headers=_auth(origin="https://evil.example"))
    p._dispatch_session("GET")
    assert p.status == 403


def test_absent_origin_allowed_for_non_browser_client():
    # curl / automated caller: no Origin, no Sec-Fetch-Site -> allowed.
    p = _Probe("/session/list", headers={"Authorization": "Bearer " + TOKEN})
    p._do_list = lambda: p._session_json(200, {"sessions": []})
    p._dispatch_session("GET")
    assert p.status == 200


def test_cross_site_fetch_without_origin_is_403():
    p = _Probe("/session/list", headers={"Authorization": "Bearer " + TOKEN,
                                         "Sec-Fetch-Site": "cross-site"})
    p._dispatch_session("GET")
    assert p.status == 403


# --- CORS: reflect only the allowlisted origin, NEVER `*` ---------------------

def test_cors_reflects_only_allowed_origin_never_star():
    p = _Probe("/session/list", headers=_auth())
    p._do_list = lambda: p._session_json(200, {"sessions": []})
    p._dispatch_session("GET")
    assert p.sent_headers.get("Access-Control-Allow-Origin") == ORIGIN
    assert p.sent_headers.get("Access-Control-Allow-Origin") != "*"
    assert p.sent_headers.get("Vary") == "Origin"


def test_allowed_origins_unions_both_env_vars(monkeypatch):
    monkeypatch.setenv("WCB_ALLOWED_ORIGINS", "http://a.example")
    monkeypatch.setenv("WCB_RWA_ORIGIN", "http://b.example")
    p = _Probe("/session/list", headers=_auth(origin="http://b.example"))
    assert p._allowed_origins() == {"http://a.example", "http://b.example"}
    assert p._origin_ok() is True


# --- routing -----------------------------------------------------------------

def test_get_non_list_route_is_405():
    p = _Probe("/session/capture", headers=_auth())
    p._dispatch_session("GET")
    assert p.status == 405


def test_unknown_post_route_is_404():
    p = _Probe("/session/bogus", headers=_auth(), body=b"{}")
    p._dispatch_session("POST")
    assert p.status == 404


# --- the exception -> HTTP error ladder --------------------------------------

LADDER = [
    (paths.EnvelopeRejected("x"), 422),
    (paths.EnvelopeNotWritten("x"), 404),
    (paths.EnvelopeIncomplete("x"), 404),
    (sr.DocNotStaged("x"), 409),
    (sr.SessionBusy("x"), 409),
    (sr.MaxSessionsReached("x"), 429),
    (PermissionError("cap mismatch"), 403),
    (sr._NotFound("x"), 404),
    (paths.PathSafetyError("x"), 403),
    (ValueError("bad"), 400),
    (TypeError("bad"), 400),
    (KeyError("bad"), 400),
    (RuntimeError("boom"), 500),
]


@pytest.mark.parametrize("exc,status", LADDER)
def test_error_ladder_maps_exception_to_status(exc, status):
    p = _Probe("/session/capture", headers=_auth(), body=b"{}")

    def _boom(sid, cap):
        raise exc

    p._do_capture = _boom
    p._dispatch_session("POST")
    assert p.status == status, "%r should map to %d, got %d" % (exc, status, p.status)


def test_body_too_large_is_413(monkeypatch):
    p = _Probe("/session/capture", headers={"Authorization": "Bearer " + TOKEN,
                                            "Origin": ORIGIN,
                                            "Content-Length": str(se.MAX_BODY_BYTES + 1)})
    p._dispatch_session("POST")
    assert p.status == 413


def test_tmux_error_maps_to_502():
    p = _Probe("/session/capture", headers=_auth(), body=b"{}")

    def _boom(sid, cap):
        raise se._TmuxError("connection reset", retryable=True)

    p._do_capture = _boom
    p._dispatch_session("POST")
    assert p.status == 502


def test_tmux_missing_maps_to_503():
    p = _Probe("/session/capture", headers=_auth(), body=b"{}")

    def _boom(sid, cap):
        raise se._TmuxError("tmux binary not found: x", retryable=False)

    p._do_capture = _boom
    p._dispatch_session("POST")
    assert p.status == 503


def test_tmux_session_gone_maps_to_502_not_503():
    # A 'session not found' tmux error must stay 502 — only a MISSING BINARY is
    # 503. The narrow 'binary not found' sniff must not catch session-gone text.
    p = _Probe("/session/capture", headers=_auth(), body=b"{}")

    def _boom(sid, cap):
        raise se._TmuxError("session not found: t", retryable=False)

    p._do_capture = _boom
    p._dispatch_session("POST")
    assert p.status == 502


def test_non_dict_json_body_is_400_not_500():
    # A valid-but-non-object JSON body (42, "x", [1,2], true) must be a clean 400.
    p = _Probe("/session/capture", headers=_auth(), body=b"42")
    p._dispatch_session("POST")
    assert p.status == 400


# --- cap matrix: every cap-bound route funnels through _authz_session --------

CAP_ROUTES = [
    ("/session/capture", {}),
    ("/session/send-key", {"keys": ["Enter"]}),
    ("/session/interrupt", {}),
    ("/session/replay", {"from_offset": 0}),
    ("/session/delete", {}),
    ("/session/get-envelope", {"turn_uuid": "12345678-1234-1234-1234-1234567890ab"}),
    ("/session/stream", {"doc": "<html></html>", "instruction": "x"}),
]


@pytest.mark.parametrize("path,extra", CAP_ROUTES)
def test_cap_bound_route_calls_authz_session_before_work(path, extra):
    body = {"session_id": "a" * 32, "cap": "the-cap"}
    body.update(extra)
    p = _Probe(path, headers=_auth(), body=json.dumps(body).encode())
    seen = {}

    def spy(sid, cap):
        seen["sid"] = sid
        seen["cap"] = cap
        raise PermissionError("cap mismatch")        # the gate fires -> 403

    p._authz_session = spy
    p._dispatch_session("POST")
    assert seen.get("sid") == "a" * 32
    assert seen.get("cap") == "the-cap"
    assert p.status == 403, "%s must reject a bad cap with 403" % path


def test_create_is_cap_exempt(monkeypatch):
    import types
    stub = types.SimpleNamespace(sid="f" * 32, cap="c" * 64,
                                 rendezvous_dir="/d", created_at=1.0)
    monkeypatch.setattr(se.REGISTRY, "create", lambda **kw: stub)

    def _forbidden(*a):
        raise AssertionError("create must NOT call _authz_session")

    p = _Probe("/session/create", headers=_auth(), body=b'{"cwd":"/tmp"}')
    p._authz_session = _forbidden
    p._dispatch_session("POST")
    assert p.status == 200


def test_list_is_cap_exempt(monkeypatch):
    monkeypatch.setattr(se.REGISTRY, "list_sessions", lambda: [])

    def _forbidden(*a):
        raise AssertionError("list must NOT call _authz_session")

    p = _Probe("/session/list", headers=_auth())
    p._authz_session = _forbidden
    p._dispatch_session("GET")
    assert p.status == 200
