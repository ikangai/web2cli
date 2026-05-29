import json
import os
import threading
import time
import pytest
import paths
import session_registry as reg

UUID = "12345678-1234-1234-1234-1234567890ab"


def _sess(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    s = os.path.join(b, "s"); os.mkdir(s, 0o700)
    return b, s


def _write_then_rename(sess, turn_uuid, obj, delay):
    time.sleep(delay)
    final = paths.env_path(sess, turn_uuid)
    part = final + ".part"
    raw = json.dumps(obj).encode()
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.write(fd, raw); os.close(fd)
    os.replace(part, final)


def test_await_envelope_returns_bytes_after_rename(tmp_path):
    base, sess = _sess(tmp_path)
    obj = {"tool": "apply_edits", "envelope": {"v": 1}, "turn_uuid": UUID,
           "gen": UUID}
    send_time = time.time()
    t = threading.Thread(target=_write_then_rename,
                         args=(sess, UUID, obj, 0.2))
    t.start()
    raw = reg.await_envelope(sess, base, UUID, send_time,
                            deadline=time.monotonic() + 5.0,
                            stable_ms=120)
    t.join()
    assert json.loads(raw)["turn_uuid"] == UUID
    assert raw == json.dumps(obj).encode()


def test_await_envelope_no_write_raises_not_written(tmp_path):
    base, sess = _sess(tmp_path)
    with pytest.raises(paths.EnvelopeNotWritten):
        reg.await_envelope(sess, base, UUID, time.time(),
                          deadline=time.monotonic() + 0.5, stable_ms=120)


def test_await_envelope_ignores_stale_older_than_send_time(tmp_path):
    base, sess = _sess(tmp_path)
    # a pre-existing env from a previous incarnation with the same uuid
    stale = paths.env_path(sess, UUID)
    fd = os.open(stale, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.write(fd, json.dumps({"tool": "x", "envelope": {"a": 1},
                            "turn_uuid": UUID, "gen": UUID}).encode())
    os.close(fd)
    old = time.time() - 100
    os.utime(stale, (old, old))
    send_time = time.time()
    # no fresh write happens -> must NOT return the stale file
    with pytest.raises(paths.EnvelopeNotWritten):
        reg.await_envelope(sess, base, UUID, send_time,
                          deadline=time.monotonic() + 0.6, stable_ms=120)
