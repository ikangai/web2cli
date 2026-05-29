import os
import stat
import pytest

import session_registry as sr
import paths

from conftest import requires_tmux   # bare import (import-mode pinned in conftest)


@requires_tmux
def test_create_mints_and_confines(tmp_base, fake_socket, fake_claude_argv, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    cwd = str(tmp_base)            # a real, confined, existing dir
    s = reg.create(cwd=cwd, cols=120, rows=40, claude_argv=fake_claude_argv,
                   socket_override=fake_socket)
    try:
        assert paths.SESSION_ID_RE.match(s.sid)
        assert len(s.cap) == 64
        assert len(s.nonce) == 16              # token_hex(8)
        st = os.lstat(s.rendezvous_dir)
        assert stat.S_ISDIR(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o700
        assert st.st_uid == os.getuid()
        assert s.rendezvous_dir.startswith(str(tmp_base))
        cap_file = os.path.join(s.rendezvous_dir, "cap")
        cst = os.lstat(cap_file)
        assert stat.S_IMODE(cst.st_mode) == 0o600
        with open(cap_file) as f:
            assert f.read() == s.cap
        assert reg._sessions[s.sid] is s
        assert s.status == "READY"
        assert s.tmux.has_session("t") is True
    finally:
        s.tmux.kill_server()


@requires_tmux
def test_create_rejects_nonexistent_cwd(tmp_base, fake_socket, fake_claude_argv, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    with pytest.raises((ValueError, NotADirectoryError, FileNotFoundError)):
        reg.create(cwd=str(tmp_base / "does-not-exist"), cols=80, rows=24,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)


# --- cwd confinement (WCB_ALLOWED_CWD_ROOT) — security hardening -------------
# These rejection cases raise at the cwd gate BEFORE any tmux launch, so they
# need no tmux. With bypassPermissions the cwd is the agent's reach.

def test_create_rejects_cwd_outside_allowed_root(tmp_path, monkeypatch):
    root = tmp_path / "allowed"; root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    monkeypatch.setenv("WCB_ALLOWED_CWD_ROOT", str(root))
    reg = sr._Registry(base=str(tmp_path))
    with pytest.raises(ValueError):
        reg.create(cwd=str(outside), cols=80, rows=24,
                   claude_argv=["bash", "-c", ":"], socket_override="wcb_cwd1")


def test_create_rejects_cwd_symlink_escape(tmp_path, monkeypatch):
    root = tmp_path / "allowed"; root.mkdir()
    secret = tmp_path / "secret"; secret.mkdir()
    link = root / "esc"
    link.symlink_to(secret)               # inside root by name, escapes by realpath
    monkeypatch.setenv("WCB_ALLOWED_CWD_ROOT", str(root))
    reg = sr._Registry(base=str(tmp_path))
    with pytest.raises(ValueError):
        reg.create(cwd=str(link), cols=80, rows=24,
                   claude_argv=["bash", "-c", ":"], socket_override="wcb_cwd2")


def test_create_unset_root_allows_any_existing_dir(tmp_path, monkeypatch):
    # Default-off: with WCB_ALLOWED_CWD_ROOT unset, the cwd gate does not fire
    # (a non-dir still fails; an existing dir passes the gate). We assert the
    # gate is a no-op by reaching the NEXT failure (max-sessions) for an
    # arbitrary existing dir outside any root.
    monkeypatch.delenv("WCB_ALLOWED_CWD_ROOT", raising=False)
    monkeypatch.setattr(sr, "MAX_SESSIONS", 0)
    reg = sr._Registry(base=str(tmp_path))
    with pytest.raises(sr._MaxSessionsReached):     # NOT ValueError -> cwd gate passed
        reg.create(cwd=str(tmp_path), cols=80, rows=24,
                   claude_argv=["bash", "-c", ":"], socket_override="wcb_cwd3")


@requires_tmux
def test_create_accepts_cwd_under_allowed_root(tmp_base, fake_socket,
                                               fake_claude_argv, monkeypatch):
    sub = tmp_base / "proj"; sub.mkdir()
    monkeypatch.setenv("WCB_ALLOWED_CWD_ROOT", str(tmp_base))
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    s = reg.create(cwd=str(sub), cols=100, rows=30,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)
    try:
        assert s.cwd == str(sub)
        assert s.tmux.has_session("t") is True
    finally:
        s.tmux.kill_server()


def test_create_max_sessions_429(tmp_base, fake_claude_argv, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    monkeypatch.setattr(sr, "MAX_SESSIONS", 0)
    reg = sr._Registry(base=str(tmp_base))
    with pytest.raises(sr._MaxSessionsReached):
        reg.create(cwd=str(tmp_base), cols=80, rows=24,
                   claude_argv=fake_claude_argv, socket_override="wcb_unused")


@requires_tmux
def test_create_verifies_alternate_off_and_arms_pipe(tmp_base, fake_socket,
                                                     fake_claude_argv, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    s = reg.create(cwd=str(tmp_base), cols=100, rows=30,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)
    try:
        assert s.tmux.alternate_on(s.pane) == 0
        assert os.path.exists(s.log_path)
        lst = os.lstat(s.log_path)
        assert stat.S_IMODE(lst.st_mode) == 0o600
    finally:
        s.tmux.kill_server()


def test_create_raises_on_alternate_on(tmp_base, monkeypatch):
    """Pure-stub path: a tmux whose alternate_on != 0 must abort create."""
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))

    class _AltTmux:
        def __init__(self, *a, **k): pass
        def socket_path(self): return None
        def new_session(self, name, cwd, cols, rows): return "%0"
        def has_session(self, n): return True
        def set_option(self, *a): pass
        def get_option(self, target, name): return None
        def alternate_on(self, t): return 1          # the bad case
        def pipe_pane_on(self, *a): pass
        def kill_server(self): pass

    monkeypatch.setattr(sr, "TmuxClient", lambda sock: _AltTmux())
    reg = sr._Registry(base=str(tmp_base))
    with pytest.raises(sr._AlternateScreenError):
        reg.create(cwd=str(tmp_base), cols=80, rows=24,
                   claude_argv=["bash", "-c", ":"], socket_override="wcb_alt")


@requires_tmux
def test_create_reaches_composer_ready(tmp_base, fake_socket, fake_claude_argv,
                                       monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    s = reg.create(cwd=str(tmp_base), cols=100, rows=30,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)
    try:
        screen = s.tmux.capture_pane(s.pane)
        from fsm import FOOTER_IDLE
        assert FOOTER_IDLE in screen
        assert s.composer_seen is True
    finally:
        s.tmux.kill_server()


@requires_tmux
def test_create_answers_trust_prompt(tmp_base, fake_socket, fake_claude_argv,
                                     monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    monkeypatch.setenv("WCB_FAKE_TRUST", "1")
    reg = sr._Registry(base=str(tmp_base))
    s = reg.create(cwd=str(tmp_base), cols=100, rows=30,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)
    try:
        screen = s.tmux.capture_pane(s.pane)
        from fsm import FOOTER_IDLE, TRUST_PROMPT
        assert FOOTER_IDLE in screen
        assert TRUST_PROMPT not in screen
    finally:
        s.tmux.kill_server()


def test_readiness_timeout_raises(tmp_base, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))

    class _NeverReady:
        def __init__(self, *a, **k): pass
        def socket_path(self): return None
        def new_session(self, name, cwd, cols, rows): return "%0"
        def has_session(self, n): return True
        def set_option(self, *a): pass
        def get_option(self, target, name): return None
        def alternate_on(self, t): return 0
        def pipe_pane_on(self, *a): pass
        def kill_server(self): pass
        def capture_pane(self, t): return "still starting\n"
        def send_keys(self, *a): pass
        def send_text(self, *a): pass

    monkeypatch.setattr(sr, "TmuxClient", lambda sock: _NeverReady())
    monkeypatch.setattr(sr, "READY_TIMEOUT", 0.3)
    monkeypatch.setattr(sr, "READY_POLL", 0.05)
    reg = sr._Registry(base=str(tmp_base))
    with pytest.raises(sr._ReadinessTimeout):
        reg.create(cwd=str(tmp_base), cols=80, rows=24,
                   claude_argv=["bash", "-c", ":"], socket_override="wcb_nr")


@requires_tmux
def test_delete_tears_down_confined(tmp_base, fake_socket, fake_claude_argv,
                                    monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    s = reg.create(cwd=str(tmp_base), cols=80, rows=24,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)
    rdir = s.rendezvous_dir
    assert os.path.isdir(rdir)
    reg.delete(s.sid, s.cap)
    assert s.tmux.has_session("t") is False
    assert not os.path.exists(rdir)
    assert s.sid not in reg._sessions
