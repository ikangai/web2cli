"""Public-API reconciliation on _Registry needed by the HTTP layer.

The cleanup-security dispatcher catches `session_registry.MaxSessionsReached`
(public, no underscore) and the capture/replay handlers ask the registry for a
rotation-adjusted log offset via `current_offset(sess)`. Both must exist
without breaking the existing `_MaxSessionsReached` callers (create()).
"""
import types

import session_registry as sr


def test_max_sessions_public_and_private_name_are_the_same_class():
    # Task 49/50 import/catch the public name; create() still raises the
    # underscore name. They MUST be the same class so one except clause covers
    # both code paths.
    assert sr.MaxSessionsReached is sr._MaxSessionsReached
    assert issubclass(sr.MaxSessionsReached, Exception)


def test_current_offset_is_filesize_plus_rotation_base(tmp_path):
    reg = sr._Registry(base=str(tmp_path))
    log = tmp_path / "log"
    log.write_bytes(b"0123456789")          # 10 bytes on disk
    sess = types.SimpleNamespace(log_path=str(log), log_offset_base=5)
    # global offset = bytes-on-disk + bytes-already-rotated-away
    assert reg.current_offset(sess) == 15


def test_current_offset_missing_log_returns_base(tmp_path):
    reg = sr._Registry(base=str(tmp_path))
    sess = types.SimpleNamespace(log_path=str(tmp_path / "absent"),
                                 log_offset_base=7)
    assert reg.current_offset(sess) == 7


def test_current_offset_none_log_returns_base(tmp_path):
    reg = sr._Registry(base=str(tmp_path))
    sess = types.SimpleNamespace(log_path=None, log_offset_base=0)
    assert reg.current_offset(sess) == 0
