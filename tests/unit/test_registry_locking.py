import threading
import time
import types
import pytest

import session_registry as sr

_COMPOSER_SCREEN = (
    "─" * 40 + "\n"
    "❯ \n"
    + "─" * 40 + "\n"
    "  " + "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents" + "\n"
)


class _StubTmux:
    """Records calls; the structural lock must NEVER wrap these."""
    def __init__(self):
        self.calls = []
        self._option = {}

    def has_session(self, name):
        self.calls.append(("has_session", name))
        return True

    def get_option(self, name):
        self.calls.append(("get_option", name))
        return self._option.get(name)

    def set_option(self, name, value):
        self.calls.append(("set_option", name, value))
        self._option[name] = value

    def capture_pane(self, target):
        self.calls.append(("capture_pane", target))
        return _COMPOSER_SCREEN


def _mk_session(**over):
    defaults = dict(
        sid="a" * 32, cap="c" * 64, nonce="d" * 16,
        socket="wcb_" + "a" * 32, pane="%0",
        cwd="/tmp/x", rendezvous_dir="/base/wcb_x_d", log_path="/base/log",
        created_at=123.0, tmux=_StubTmux(),
    )
    defaults.update(over)
    return sr._Session(**defaults)


def test_session_fields_and_locks_present():
    s = _mk_session()
    assert s.sid == "a" * 32
    assert s.cap == "c" * 64
    assert s.status == "RECONSTRUCTING"        # default until hydrated
    assert isinstance(s.turn_lock, type(threading.Lock()))
    assert isinstance(s.ready, threading.Event)
    assert not s.ready.is_set()


def test_session_consolidated_fields_present():
    """CRITIQUE-FIX: all fields siblings depend on exist from Task 10."""
    s = _mk_session()
    assert s.shell_pid is None
    assert s.composer_seen is False
    assert s._gone_strikes == 0
    assert s.log_offset_base == 0
    assert s._claimed is False


def test_is_busy_lock_held():
    s = _mk_session()
    assert s.is_busy() is False
    s.turn_lock.acquire()
    try:
        assert s.is_busy() is True
    finally:
        s.turn_lock.release()


def test_is_busy_wcb_turn_option_set():
    s = _mk_session()
    assert s.is_busy() is False
    s.tmux.set_option("@wcb_turn", "11111111-1111-1111-1111-111111111111")
    assert s.is_busy() is True
    s.tmux.set_option("@wcb_turn", "")        # cleared
    assert s.is_busy() is False


def test_is_busy_fsm_not_idle():
    s = _mk_session()
    s._classify_state = lambda: "thinking"
    assert s.is_busy() is True
    s._classify_state = lambda: "idle"
    assert s.is_busy() is False


def test_registry_structural_lock_guards_dict_only():
    reg = sr._Registry()
    assert reg._sessions == {}
    assert isinstance(reg._lock, type(threading.Lock()))


def test_registry_base_param_and_self_base(tmp_path, monkeypatch):
    """CRITIQUE-FIX: pinned constructor — base kwarg stored on self._base."""
    monkeypatch.setattr(sr.paths, "base_dir", lambda: "/default/base")
    reg_default = sr._Registry()
    assert reg_default._base == "/default/base"          # falls back to base_dir()
    reg_explicit = sr._Registry(base=str(tmp_path))
    assert reg_explicit._base == str(tmp_path)            # explicit wins


def test_registry_structural_lock_never_held_during_tmux_io():
    """A blocking-tmux stub: assert the structural lock is FREE whenever tmux
    I/O happens by checking lock.locked() from inside the stub."""
    reg = sr._Registry()
    observed = []

    class _BlockingTmux(_StubTmux):
        def has_session(self, name):
            observed.append(reg._lock.locked())   # must be False
            return True

    s = _mk_session(tmux=_BlockingTmux())
    with reg._lock:
        reg._sessions[s.sid] = s
    alive = reg._alive_ids([s])
    assert alive == [s.sid]
    assert observed == [False]


def test_registry_module_singleton_exists():
    """CRITIQUE-FIX: single canonical singleton name is REGISTRY."""
    assert isinstance(sr.REGISTRY, sr._Registry)
