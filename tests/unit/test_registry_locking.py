import threading
import time
import types
import pytest

import paths
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

    def get_option(self, target, name):
        self.calls.append(("get_option", name))
        return self._option.get(name)

    def set_option(self, target, name, value):
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
    s.tmux.set_option("t", "@wcb_turn", "11111111-1111-1111-1111-111111111111")
    assert s.is_busy() is True
    s.tmux.set_option("t", "@wcb_turn", "")        # cleared
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


def test_get_or_reconstruct_single_winner_under_concurrency(monkeypatch):
    """N threads racing get_or_reconstruct(sid) must share ONE _Session and
    ONE turn_lock; only ONE thread hydrates (design risk #5)."""
    reg = sr._Registry()
    sid = "b" * 32

    monkeypatch.setattr(sr.paths, "validate_session_id", lambda x: None)
    hydrate_calls = []
    barrier = threading.Barrier(8)

    def fake_hydrate(s):
        hydrate_calls.append(s.sid)
        time.sleep(0.05)            # widen the race window
        s.pane = "%0"
        s.status = "READY"

    monkeypatch.setattr(reg, "_hydrate", fake_hydrate)

    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        s = reg.get_or_reconstruct(sid)
        with lock:
            results.append(s)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert hydrate_calls == [sid]
    first = results[0]
    assert all(r is first for r in results)
    assert all(r.turn_lock is first.turn_lock for r in results)
    assert first.ready.is_set()
    assert first.status == "READY"


def test_get_or_reconstruct_returns_existing_ready(monkeypatch):
    reg = sr._Registry()
    monkeypatch.setattr(sr.paths, "validate_session_id", lambda x: None)
    s = _mk_session(sid="e" * 32)
    s.status = "READY"
    s.ready.set()
    with reg._lock:
        reg._sessions[s.sid] = s
    called = []
    monkeypatch.setattr(reg, "_hydrate", lambda x: called.append(1))
    got = reg.get_or_reconstruct("e" * 32)
    assert got is s
    assert called == []            # already READY -> no hydrate


def test_hydrate_rejects_tampered_nonce_outside_base(tmp_base, monkeypatch):
    """CRITIQUE-FIX risk #14: a tampered @wcb_nonce must be rejected."""
    monkeypatch.setattr(sr.paths, "base_dir", lambda: str(tmp_base))

    class _TamperTmux(_StubTmux):
        def has_session(self, name):
            return True
        def pane_id(self, name):
            return "%0"

    reg = sr._Registry(base=str(tmp_base))
    s = _mk_session(sid="9" * 32, nonce=None, rendezvous_dir=None,
                    tmux=_TamperTmux())
    s.tmux.set_option("t", "@wcb_nonce", "../../etc")
    with pytest.raises(sr._RendezvousRedirect):
        reg._hydrate(s)


def test_list_sessions_reports_busy_when_lock_held():
    reg = sr._Registry()
    s = _mk_session(sid="f" * 32)
    s.status = "READY"
    s._classify_state = lambda: "idle"
    with reg._lock:
        reg._sessions[s.sid] = s

    rows = reg.list_sessions()
    assert len(rows) == 1
    assert rows[0]["session_id"] == "f" * 32
    assert rows[0]["state"] in ("idle", "ready")
    assert rows[0]["alive"] is True

    reg._list_cache = None        # bypass the brief cache for this assertion
    s.turn_lock.acquire()
    try:
        rows = reg.list_sessions()
        assert rows[0]["state"] == "busy"      # turn lock held -> busy
    finally:
        s.turn_lock.release()


def test_list_sessions_never_holds_structural_lock_during_tmux():
    reg = sr._Registry()
    observed = []

    class _Probe(_StubTmux):
        def has_session(self, name):
            observed.append(reg._lock.locked())
            return True
        def capture_pane(self, target):
            observed.append(reg._lock.locked())
            return ""

    s = _mk_session(sid="0" * 32, tmux=_Probe())
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    reg.list_sessions()
    assert observed and all(held is False for held in observed)


def test_list_sessions_marks_dead_alive_false_without_evicting():
    reg = sr._Registry()

    class _Dead(_StubTmux):
        def has_session(self, name):
            return False

    s = _mk_session(sid="1" * 32, tmux=_Dead())
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    rows = reg.list_sessions()
    assert rows[0]["alive"] is False
    assert s.sid in reg._sessions          # list NEVER evicts


def test_list_sessions_log_bytes_includes_offset_base(tmp_base, monkeypatch):
    """CRITIQUE-FIX: log_bytes accounts for log_offset_base (rotation)."""
    monkeypatch.setattr(sr.paths, "base_dir", lambda: str(tmp_base))
    log = tmp_base / "log"
    log.write_bytes(b"x" * 100)
    reg = sr._Registry(base=str(tmp_base))
    s = _mk_session(sid="2" * 32, log_path=str(log))
    s.status = "READY"
    s._classify_state = lambda: "idle"
    s.log_offset_base = 40                 # 40 bytes rotated away earlier
    with reg._lock:
        reg._sessions[s.sid] = s
    rows = reg.list_sessions()
    assert rows[0]["log_bytes"] == 140     # 100 on disk + 40 base


def test_list_sessions_brief_cache(monkeypatch):
    """CRITIQUE-FIX design (4): /list cached briefly to avoid O(N) fan-out."""
    reg = sr._Registry()
    probes = []

    class _Counting(_StubTmux):
        def has_session(self, name):
            probes.append(1)
            return True

    s = _mk_session(sid="3" * 32, tmux=_Counting())
    s.status = "READY"
    s._classify_state = lambda: "idle"
    with reg._lock:
        reg._sessions[s.sid] = s
    monkeypatch.setattr(sr, "LIST_CACHE_TTL", 60.0)
    reg.list_sessions()
    reg.list_sessions()                    # served from cache, no new probe
    assert sum(probes) == 1
    monkeypatch.setattr(sr, "LIST_CACHE_TTL", 0.0)   # expire immediately
    reg.list_sessions()
    assert sum(probes) == 2


def test_delete_rejects_cap_mismatch():
    reg = sr._Registry()
    s = _mk_session(sid="2" * 32, cap="right" + "0" * 59)
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    with pytest.raises(PermissionError):
        reg.delete("2" * 32, "wrong" + "0" * 59)
    assert s.sid in reg._sessions          # not torn down on cap mismatch


def test_delete_refuses_when_busy():
    reg = sr._Registry()
    s = _mk_session(sid="3" * 32, cap="k" * 64)
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    s.turn_lock.acquire()
    try:
        with pytest.raises(sr._SessionBusy):
            reg.delete("3" * 32, "k" * 64)
    finally:
        s.turn_lock.release()
    assert s.sid in reg._sessions


def test_delete_refuses_unconfined_dir(tmp_base, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    killed = []

    class _T(_StubTmux):
        def kill_server(self):
            killed.append(True)
        def socket_path(self):
            return None

    s = _mk_session(sid="4" * 32, cap="k" * 64,
                    rendezvous_dir="/etc", tmux=_T())
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    with pytest.raises(PermissionError):
        reg.delete("4" * 32, "k" * 64)
    import os as _os
    assert _os.path.isdir("/etc")          # /etc must NOT have been rm'd
