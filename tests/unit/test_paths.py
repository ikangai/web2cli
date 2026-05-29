"""Path-safety + identity helpers (paths.py).

This module is a build-order gap-fill: the plan references paths.py as a hard
dependency of the registry (Task 11/12/17/18) but never authored it. Security-
critical, so the confinement / base-dir / id-validation paths are tested hard.
"""
import os
import stat
import uuid

import pytest

import paths


# --- minting ----------------------------------------------------------------

def test_mint_session_id_is_32_hex_and_matches_re():
    sid = paths.mint_session_id()
    assert len(sid) == 32
    assert all(c in "0123456789abcdef" for c in sid)
    assert paths.SESSION_ID_RE.match(sid)


def test_cap_and_nonce_lengths():
    assert len(paths.mint_cap()) == 64       # secrets.token_hex(32)
    assert len(paths.mint_nonce()) == 16     # secrets.token_hex(8)


def test_mints_are_unique():
    assert paths.mint_session_id() != paths.mint_session_id()
    assert paths.mint_cap() != paths.mint_cap()
    assert paths.mint_nonce() != paths.mint_nonce()


# --- id validation (never let a caller id reach a path/argv unvalidated) ----

def test_validate_session_id_accepts_minted():
    sid = paths.mint_session_id()
    assert paths.validate_session_id(sid) == sid


@pytest.mark.parametrize("bad", [
    "", "x" * 32, "A" * 32, "0" * 31, "0" * 33, "../../etc/passwd",
    "abc", "0" * 32 + "/evil", "wcb_" + "0" * 32, 123, None,
])
def test_validate_session_id_rejects_bad(bad):
    with pytest.raises(ValueError):
        paths.validate_session_id(bad)


def test_validate_turn_uuid_accepts_uuid4_rejects_bad():
    assert paths.validate_turn_uuid(str(uuid.uuid4()))
    for bad in ["", "not-a-uuid", "0" * 32, "X" * 36, 123, None]:
        with pytest.raises(ValueError):
            paths.validate_turn_uuid(bad)


# --- base dir ---------------------------------------------------------------

def test_base_dir_is_under_home_not_tmp():
    b = paths.base_dir()
    assert b.startswith(os.path.expanduser("~"))
    assert not b.startswith("/tmp")


def test_verify_base_dir_accepts_0700_owned_dir(tmp_base):
    paths.verify_base_dir(str(tmp_base))     # must not raise


def test_verify_base_dir_rejects_loose_mode(tmp_path):
    d = tmp_path / "loose"
    d.mkdir()
    os.chmod(d, 0o755)
    with pytest.raises(paths.PathSafetyError):
        paths.verify_base_dir(str(d))


def test_verify_base_dir_rejects_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    os.chmod(real, 0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(paths.PathSafetyError):
        paths.verify_base_dir(str(link))


def test_verify_base_dir_rejects_nondir(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    os.chmod(f, 0o700)
    with pytest.raises(paths.PathSafetyError):
        paths.verify_base_dir(str(f))


def test_verify_base_dir_missing_raises(tmp_path):
    with pytest.raises((paths.PathSafetyError, FileNotFoundError)):
        paths.verify_base_dir(str(tmp_path / "nope"))


# --- session_dir + confinement ---------------------------------------------

def test_session_dir_format_and_under_base(tmp_path):
    sid = paths.mint_session_id()
    nonce = paths.mint_nonce()
    d = paths.session_dir(str(tmp_path), sid, nonce)
    assert d == os.path.join(str(tmp_path), "wcb_%s_%s" % (sid, nonce))
    assert d.startswith(str(tmp_path))


def test_assert_confined_accepts_base_and_children(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    paths.assert_confined(str(base), str(base))            # base itself
    paths.assert_confined(str(base / "wcb_x_y"), str(base))  # non-existent child OK


def test_assert_confined_rejects_escape(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "outside").mkdir()
    with pytest.raises(paths.PathSafetyError):
        paths.assert_confined(str(tmp_path / "outside"), str(base))
    with pytest.raises(paths.PathSafetyError):
        paths.assert_confined(str(base / ".." / "evil"), str(base))


def test_assert_confined_rejects_symlink_escape(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    link = base / "escape"
    link.symlink_to(outside)
    # realpath follows the symlink out of base -> must be refused.
    with pytest.raises(paths.PathSafetyError):
        paths.assert_confined(str(link / "x"), str(base))
