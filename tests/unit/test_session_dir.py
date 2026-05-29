import os
import stat
import pytest
import paths
import session_registry as reg


def test_session_dir_shape():
    sid = "a" * 32
    nonce = "deadbeefdeadbeef"
    d = paths.session_dir("/base", sid, nonce)
    assert d == f"/base/wcb_{sid}_{nonce}"


def test_ensure_session_dir_creates_0700_confined(tmp_path):
    base = str(tmp_path / "rv")
    os.mkdir(base, 0o700); os.chmod(base, 0o700)
    sid = "b" * 32
    nonce = paths.mint_nonce()
    d = reg._ensure_session_dir(base, sid, nonce)
    st = os.lstat(d)
    assert stat.S_ISDIR(st.st_mode)
    assert stat.S_IMODE(st.st_mode) == 0o700
    assert os.path.realpath(d).startswith(os.path.realpath(base) + os.sep)


def test_ensure_session_dir_rejects_escape(tmp_path):
    base = str(tmp_path / "rv")
    os.mkdir(base, 0o700); os.chmod(base, 0o700)
    # a malformed (non-16-hex) nonce must be refused with ValueError.
    with pytest.raises((PermissionError, ValueError)):
        reg._ensure_session_dir(base, "c" * 32, "../escape")
