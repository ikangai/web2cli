import os
import stat
import pytest
import paths


def _base(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    return b


UUID = "12345678-1234-1234-1234-1234567890ab"


def test_doc_path_shape():
    p = paths.doc_path("/sess", UUID)
    assert p == f"/sess/doc.{UUID}.html"


def test_put_doc_writes_bytes_exact_then_renames(tmp_path):
    base = _base(tmp_path)
    sess = os.path.join(base, "wcb_" + "a" * 32 + "_" + "b" * 16)
    os.mkdir(sess, 0o700)
    payload = b"<html>\n<body>hi</body>\n</html>\n"
    final = paths.put_doc(sess, base, UUID, payload)
    assert final == paths.doc_path(sess, UUID)
    # the .part scratch file is gone after rename
    assert not os.path.exists(final + ".part")
    with open(final, "rb") as fh:
        assert fh.read() == payload
    # 0600, single link, owned by us
    st = os.lstat(final)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 1
    assert st.st_uid == os.getuid()


def test_put_doc_rejects_carriage_return(tmp_path):
    base = _base(tmp_path)
    sess = os.path.join(base, "s"); os.mkdir(sess, 0o700)
    with pytest.raises(ValueError):
        paths.put_doc(sess, base, UUID, b"line1\r\nline2\n")


def test_put_doc_rejects_bad_uuid(tmp_path):
    base = _base(tmp_path)
    sess = os.path.join(base, "s"); os.mkdir(sess, 0o700)
    with pytest.raises(ValueError):
        paths.put_doc(sess, base, "../etc/passwd", b"x")


def test_put_doc_overwrites_prior_doc_atomically(tmp_path):
    base = _base(tmp_path)
    sess = os.path.join(base, "s"); os.mkdir(sess, 0o700)
    paths.put_doc(sess, base, UUID, b"old\n")
    final = paths.put_doc(sess, base, UUID, b"new\n")
    with open(final, "rb") as fh:
        assert fh.read() == b"new\n"
