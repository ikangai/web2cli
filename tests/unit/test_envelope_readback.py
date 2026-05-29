import json
import os
import pytest
import paths

UUID = "12345678-1234-1234-1234-1234567890ab"
# The exact 28-byte, no-trailing-newline calibration payload, wrapped in the
# envelope shape the bridge expects.
INNER = '{"ok":true,"probe":"wcbcal"}'


def _sess(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    s = os.path.join(b, "s"); os.mkdir(s, 0o700)
    return b, s


def _write_env(sess, turn_uuid, obj, mode=0o600):
    p = paths.env_path(sess, turn_uuid)
    raw = json.dumps(obj).encode()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    os.write(fd, raw); os.close(fd)
    os.chmod(p, mode)
    return p, raw


def test_env_path_shape():
    assert paths.env_path("/s", UUID) == f"/s/env.{UUID}.json"


def test_byte_exact_readback(tmp_path):
    base, sess = _sess(tmp_path)
    obj = {"tool": "apply_edits", "envelope": {"v": 1}, "turn_uuid": UUID,
           "gen": UUID, "probe": INNER}
    p, raw = _write_env(sess, UUID, obj)
    got = paths.read_envelope_bytes(p, UUID)
    assert got == raw                     # verbatim, no re-serialization
    assert isinstance(got, bytes)


def test_no_trailing_newline_preserved(tmp_path):
    base, sess = _sess(tmp_path)
    p = paths.env_path(sess, UUID)
    raw = (b'{"tool":"x","envelope":{"a":1},"turn_uuid":"' + UUID.encode()
           + b'","gen":"' + UUID.encode() + b'"}')
    assert not raw.endswith(b"\n")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.write(fd, raw); os.close(fd)
    assert paths.read_envelope_bytes(p, UUID) == raw  # exactly 28-style bytes


def test_rejects_symlink(tmp_path):
    base, sess = _sess(tmp_path)
    real = os.path.join(sess, "real.json")
    with open(real, "w") as fh:
        fh.write(json.dumps({"tool": "x", "envelope": {}, "turn_uuid": UUID,
                            "gen": UUID}))
    link = paths.env_path(sess, UUID)
    os.symlink(real, link)
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(link, UUID)


def test_rejects_group_world_bits(tmp_path):
    base, sess = _sess(tmp_path)
    p, _ = _write_env(sess, UUID,
                      {"tool": "x", "envelope": {"a": 1}, "turn_uuid": UUID,
                       "gen": UUID},
                      mode=0o077)
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)


def test_accepts_owner_rw_group_other_read(tmp_path):
    # Real claude's Write tool honours the umask and lands the envelope at 0o644
    # (rw-r--r--). The session dir is 0700, so the read bits are moot; group/
    # other READ (no write) MUST be accepted or the feature never works live.
    base, sess = _sess(tmp_path)
    p, raw = _write_env(sess, UUID,
                        {"tool": "x", "envelope": {"a": 1}, "turn_uuid": UUID,
                         "gen": UUID},
                        mode=0o644)
    assert paths.read_envelope_bytes(p, UUID) == raw


def test_rejects_group_writable(tmp_path):
    # The one mode-based vector we still reject as defence-in-depth: a
    # group/other-WRITABLE envelope (the injection/tamper vector).
    base, sess = _sess(tmp_path)
    p, _ = _write_env(sess, UUID,
                      {"tool": "x", "envelope": {"a": 1}, "turn_uuid": UUID,
                       "gen": UUID},
                      mode=0o620)
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)


def test_rejects_hardlink_nlink_gt_1(tmp_path):
    base, sess = _sess(tmp_path)
    p, _ = _write_env(sess, UUID,
                      {"tool": "x", "envelope": {"a": 1}, "turn_uuid": UUID,
                       "gen": UUID})
    os.link(p, p + ".hard")               # nlink -> 2
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)


def test_rejects_turn_uuid_mismatch(tmp_path):
    base, sess = _sess(tmp_path)
    other = "ffffffff-1234-1234-1234-1234567890ab"
    p, _ = _write_env(sess, UUID,
                      {"tool": "x", "envelope": {"a": 1}, "turn_uuid": other,
                       "gen": other})
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)


def test_rejects_gen_sentinel_mismatch(tmp_path):
    base, sess = _sess(tmp_path)
    # turn_uuid is correct but the gen sentinel echo is stale -> reject (risk #4)
    p, _ = _write_env(sess, UUID,
                      {"tool": "x", "envelope": {"a": 1}, "turn_uuid": UUID,
                       "gen": "ffffffff-1111-1111-1111-111111111111"})
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)


def test_missing_file_raises_not_written(tmp_path):
    base, sess = _sess(tmp_path)
    with pytest.raises(paths.EnvelopeNotWritten):
        paths.read_envelope_bytes(paths.env_path(sess, UUID), UUID)


def test_truncated_json_raises_incomplete(tmp_path):
    base, sess = _sess(tmp_path)
    p = paths.env_path(sess, UUID)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.write(fd, b'{"tool":"x","envelope":{')  # truncated
    os.close(fd)
    with pytest.raises(paths.EnvelopeIncomplete):
        paths.read_envelope_bytes(p, UUID)


def test_rejects_missing_tool_field(tmp_path):
    base, sess = _sess(tmp_path)
    p, _ = _write_env(sess, UUID, {"envelope": {"a": 1}, "turn_uuid": UUID,
                                   "gen": UUID})
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)
