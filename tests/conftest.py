import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

TMUX = shutil.which("tmux")
requires_tmux = pytest.mark.skipif(TMUX is None, reason="tmux binary not found")

FAKE = pathlib.Path(__file__).parent / "fake_claude.sh"
DROPIN_FAKE = pathlib.Path(__file__).parent / "fake_claude_dropin.sh"
CAPTURES = pathlib.Path(__file__).parent / "fixtures" / "captures"


@pytest.fixture
def tmp_base(tmp_path):
    """A 0700 rendezvous base dir owned by euid (mimics verify_base_dir)."""
    d = tmp_path / "rendezvous"
    d.mkdir(mode=0o700)
    # mkdir mode is masked by umask; force the exact bits verify_base_dir wants.
    os.chmod(d, 0o700)
    return d


@pytest.fixture
def capture():
    """Return a reader: capture("composer_ready.txt") -> str (the sliced screen)."""
    return lambda name: (CAPTURES / name).read_text()


@pytest.fixture
def fake_socket():
    """Unique -L socket name per test; kill-server AND unlink the socket file in
    teardown so neither server processes nor dead socket files accumulate."""
    sock = "wcbtest_" + os.urandom(4).hex()
    yield sock
    if TMUX is not None:
        subprocess.run(
            [TMUX, "-L", sock, "kill-server"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        # kill-server leaves the AF_UNIX socket file behind; remove it too so
        # /tmp/tmux-<uid>/ doesn't fill with inert dead sockets across runs.
        sock_dir = os.environ.get("TMUX_TMPDIR") or "/tmp"
        sock_path = os.path.join(sock_dir, f"tmux-{os.getuid()}", sock)
        with contextlib.suppress(OSError):
            os.unlink(sock_path)


@pytest.fixture
def fake_claude_argv():
    """argv that runs the fake claude mimic instead of the real binary."""
    return ["bash", str(FAKE)]


class _HttpClient:
    """Thin urllib client returning (status, body) where body is parsed JSON
    when the response is application/json, else raw bytes."""
    def __init__(self, port, reg, base, token, origin):
        self.port = port
        self.reg = reg
        self.base = base
        self.token = token
        self.origin = origin

    _UNSET = object()

    def request(self, method, path, body=None, *, token=_UNSET, origin=_UNSET,
                raw=False, headers=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        tok = self.token if token is self._UNSET else token
        org = self.origin if origin is self._UNSET else origin
        if tok is not None:
            req.add_header("Authorization", "Bearer " + tok)
        if org is not None:
            req.add_header("Origin", org)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            payload = resp.read()
            resp_headers = dict(resp.headers)
        except urllib.error.HTTPError as e:
            status = e.code
            ctype = e.headers.get("Content-Type", "")
            payload = e.read()
            resp_headers = dict(e.headers)
        if raw:
            return status, payload, resp_headers
        if ctype.startswith("application/json") and payload:
            return status, json.loads(payload), resp_headers
        return status, payload, resp_headers

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)

    def get(self, path, **kw):
        return self.request("GET", path, None, **kw)


@pytest.fixture
def session_http(tmp_base, monkeypatch):
    """A running ThreadingHTTPServer wired to a temp-base registry, with the
    /session/* token + origin allowlist configured.

    HTTP /session/create always launches REAL claude (it never accepts a caller
    argv — risk #6), so fake-based tests create sessions DIRECTLY on the
    returned `reg` (with fake_claude_argv + a fake socket) and then drive
    stream/capture/get-envelope over HTTP against client.reg.
    """
    import paths
    import server as srv
    import session_endpoints as se
    import session_registry as sr

    token = "tok-" + os.urandom(4).hex()
    origin = "http://localhost:5173"
    monkeypatch.setenv("WEB_CLI_BRIDGE_TOKEN", token)
    monkeypatch.setenv("WCB_ALLOWED_ORIGINS", origin)
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    monkeypatch.setattr(se, "REGISTRY", reg)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield _HttpClient(port, reg, str(tmp_base), token, origin)
    finally:
        httpd.shutdown()
        httpd.server_close()
