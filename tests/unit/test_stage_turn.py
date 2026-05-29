import os
import pytest
import paths
import session_registry as reg

UUID = "12345678-1234-1234-1234-1234567890ab"


class FakeTmux:
    def __init__(self):
        self.options = {}
    def set_option(self, target, name, value):
        self.options[name] = value


def _sess(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    s = os.path.join(b, "wcb_" + "a" * 32 + "_" + "b" * 16)
    os.mkdir(s, 0o700)
    return b, s


def test_stage_turn_puts_doc_with_sentinel_and_sets_option(tmp_path):
    base, sess = _sess(tmp_path)
    tmux = FakeTmux()
    persisted = b"<html>\n<body>x</body>\n</html>\n"
    docp = reg.stage_turn(tmux, base, sess, UUID, persisted)
    assert docp == paths.doc_path(sess, UUID)
    with open(docp, "rb") as fh:
        staged = fh.read()
    assert (f"<!-- rwa:gen {UUID} -->").encode() in staged
    assert tmux.options.get("@wcb_turn") == UUID


def test_stage_turn_unlinks_prior_env_for_same_uuid(tmp_path):
    base, sess = _sess(tmp_path)
    stale = paths.env_path(sess, UUID)
    with open(stale, "w") as fh:
        fh.write('{"stale":true}')
    reg.stage_turn(FakeTmux(), base, sess, UUID, b"<p>hi</p>\n")
    assert not os.path.exists(stale)      # prior env removed at turn start


def test_stage_turn_sweeps_all_leftover_env_and_old_docs(tmp_path):
    base, sess = _sess(tmp_path)
    other = "ffffffff-1234-1234-1234-1234567890ab"
    leftover_env = paths.env_path(sess, other)
    old_doc = paths.doc_path(sess, other)
    for p in (leftover_env, old_doc):
        with open(p, "w") as fh:
            fh.write("junk")
    reg.stage_turn(FakeTmux(), base, sess, UUID, b"<p>hi</p>\n")
    assert not os.path.exists(leftover_env)
    assert not os.path.exists(old_doc)
    # the current turn's doc remains
    assert os.path.exists(paths.doc_path(sess, UUID))


def test_stage_turn_refuses_symlink_leftover(tmp_path):
    base, sess = _sess(tmp_path)
    target = str(tmp_path / "outside.json")
    with open(target, "w") as fh:
        fh.write("x")
    link = paths.env_path(sess, "aaaaaaaa-1234-1234-1234-1234567890ab")
    os.symlink(target, link)
    reg.stage_turn(FakeTmux(), base, sess, UUID, b"<p>hi</p>\n")
    # the symlink is removed but its target is never touched
    assert not os.path.lexists(link)
    assert os.path.exists(target)
