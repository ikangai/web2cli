import os
import pytest
import paths
import session_registry as reg

UUID = "12345678-1234-1234-1234-1234567890ab"


class FakeTmux:
    def __init__(self):
        self.options = {}
    def set_option(self, name, value):
        self.options[name] = value


class FakeSession:
    """Minimal stand-in for _Session: only the staged-flag surface."""
    def __init__(self):
        self.doc_staged = False   # reconstruct/fresh default: NOT staged


def _sess(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    s = os.path.join(b, "wcb_" + "a" * 32 + "_" + "b" * 16)
    os.mkdir(s, 0o700)
    return b, s


def test_assert_doc_staged_raises_when_unstaged():
    s = FakeSession()
    with pytest.raises(reg.DocNotStaged):
        reg.assert_doc_staged(s)


def test_mark_doc_staged_then_assert_passes():
    s = FakeSession()
    reg.mark_doc_staged(s)
    reg.assert_doc_staged(s)            # must not raise


def test_stage_turn_marks_doc_staged(tmp_path):
    base, sess = _sess(tmp_path)
    s = FakeSession()
    reg.stage_turn(FakeTmux(), base, sess, UUID, b"<p>hi</p>\n", session=s)
    reg.assert_doc_staged(s)            # stage_turn flipped the flag


def test_reconstruct_default_is_unstaged():
    # A freshly reconstructed session must start unstaged so the first stream
    # is refused with 409 until the rwa re-stages (design §4).
    s = FakeSession()
    assert s.doc_staged is False
    with pytest.raises(reg.DocNotStaged):
        reg.assert_doc_staged(s)
