import pytest
import paths

UUID = "12345678-1234-1234-1234-1234567890ab"


def test_inject_appends_sentinel_when_absent():
    doc = b"<html>\n<body>hi</body>\n</html>\n"
    out = paths.inject_gen_sentinel(doc, UUID)
    assert (f"<!-- rwa:gen {UUID} -->").encode() in out
    # original content preserved, LF-only
    assert out.startswith(doc.rstrip(b"\n"))
    assert b"\r" not in out


def test_inject_replaces_prior_sentinel():
    old = "11111111-1111-1111-1111-111111111111"
    doc = (b"<html>\n<!-- rwa:gen " + old.encode() + b" -->\n</html>\n")
    out = paths.inject_gen_sentinel(doc, UUID)
    assert old.encode() not in out
    assert (f"<!-- rwa:gen {UUID} -->").encode() in out
    # exactly one sentinel
    assert out.count(b"<!-- rwa:gen ") == 1


def test_verify_envelope_sentinel_accepts_matching():
    env = {"tool": "apply_edits", "envelope": {"x": 1},
           "turn_uuid": UUID, "gen": UUID}
    paths.verify_envelope_sentinel(env, UUID)  # must not raise


def test_verify_envelope_sentinel_rejects_mismatch():
    env = {"tool": "apply_edits", "envelope": {"x": 1},
           "turn_uuid": UUID, "gen": "ffffffff-1111-1111-1111-111111111111"}
    with pytest.raises(paths.EnvelopeRejected):
        paths.verify_envelope_sentinel(env, UUID)


def test_verify_envelope_sentinel_rejects_missing_gen():
    env = {"tool": "apply_edits", "envelope": {"x": 1}, "turn_uuid": UUID}
    with pytest.raises(paths.EnvelopeRejected):
        paths.verify_envelope_sentinel(env, UUID)
