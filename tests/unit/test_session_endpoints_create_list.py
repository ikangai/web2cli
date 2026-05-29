import json
import pytest

import session_endpoints as se
import session_registry as sr


class _FakeReg:
    """Stands in for the module REGISTRY; records create/list calls."""
    def __init__(self):
        self.created = []
        self.list_rows = [{"session_id": "a" * 32, "state": "idle",
                           "created_at": 1.0, "log_bytes": 0, "alive": True}]
        self.raise_max = False
        self.raise_cwd = None

    def create(self, *, cwd, cols, rows, claude_argv, **kw):
        # CRITIQUE-FIX: assert the HTTP wrapper never forwards socket_override.
        assert "socket_override" not in kw
        if self.raise_max:
            raise sr._MaxSessionsReached("max")
        if self.raise_cwd is not None:
            raise self.raise_cwd
        self.created.append((cwd, cols, rows, tuple(claude_argv)))

        class _S:
            sid = "f" * 32
            cap = "c" * 64
            rendezvous_dir = "/base/wcb_x_y"
            created_at = 1.0
        return _S()

    def list_sessions(self):
        return self.list_rows


class _Harness(se.SessionMixin):
    """Minimal handler exposing only what the wrappers touch."""
    def __init__(self, body):
        self._body = body
        self.status = None
        self.payload = None

    def _read_session_body(self):
        return self._body

    def _session_json(self, status, obj):
        self.status = status
        self.payload = obj


@pytest.fixture(autouse=True)
def _reg(monkeypatch):
    reg = _FakeReg()
    monkeypatch.setattr(se, "REGISTRY", reg)
    return reg


def test_do_create_happy_path(_reg):
    h = _Harness({"cwd": "/tmp/work", "cols": 100, "rows": 30})
    h._do_create()
    assert h.status == 200
    assert h.payload["session_id"] == "f" * 32
    assert h.payload["cap"] == "c" * 64
    assert h.payload["rendezvous_dir"] == "/base/wcb_x_y"
    assert _reg.created == [("/tmp/work", 100, 30, tuple(se.DEFAULT_CLAUDE_ARGV))]


def test_do_create_ignores_caller_socket_override(_reg):
    """risk #6: a caller-supplied socket/socket_override must be dropped."""
    h = _Harness({"cwd": "/tmp/work", "socket_override": "wcb_evil",
                  "socket": "wcb_evil"})
    h._do_create()
    assert h.status == 200          # _FakeReg.create asserts no socket_override


def test_do_create_missing_cwd_400(_reg):
    h = _Harness({})
    h._do_create()
    assert h.status == 400
    assert "cwd" in h.payload["error"]


def test_do_create_bad_cwd_400(_reg):
    _reg.raise_cwd = NotADirectoryError("/nope")
    h = _Harness({"cwd": "/nope"})
    h._do_create()
    assert h.status == 400


def test_do_create_max_sessions_429(_reg):
    _reg.raise_max = True
    h = _Harness({"cwd": "/tmp/work"})
    h._do_create()
    assert h.status == 429


def test_do_list_returns_rows(_reg):
    h = _Harness(None)
    h._do_list()
    assert h.status == 200
    assert h.payload["sessions"] == _reg.list_rows
