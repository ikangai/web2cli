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


def test_create_max_sessions_429(tmp_base, fake_claude_argv, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    monkeypatch.setattr(sr, "MAX_SESSIONS", 0)
    reg = sr._Registry(base=str(tmp_base))
    with pytest.raises(sr._MaxSessionsReached):
        reg.create(cwd=str(tmp_base), cols=80, rows=24,
                   claude_argv=fake_claude_argv, socket_override="wcb_unused")
