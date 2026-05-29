import os
import stat
import pytest
import paths


def _mk(d, mode=0o700):
    os.mkdir(d, mode)
    os.chmod(d, mode)  # defeat umask
    return d


def test_base_dir_is_under_home_not_tmp():
    b = paths.base_dir()
    home = os.path.realpath(os.path.expanduser("~"))
    assert os.path.realpath(b).startswith(home + os.sep)
    assert not os.path.realpath(b).startswith("/tmp")
    assert not os.path.realpath(b).startswith("/private/tmp")


def test_verify_base_dir_accepts_good_dir(tmp_path):
    d = _mk(str(tmp_path / "rv"))
    paths.verify_base_dir(d)  # must not raise


def test_verify_base_dir_rejects_symlink(tmp_path):
    real = _mk(str(tmp_path / "real"))
    link = str(tmp_path / "link")
    os.symlink(real, link)
    with pytest.raises(RuntimeError):
        paths.verify_base_dir(link)


def test_verify_base_dir_rejects_group_world_bits(tmp_path):
    d = _mk(str(tmp_path / "rv"), mode=0o755)
    with pytest.raises(RuntimeError):
        paths.verify_base_dir(d)


def test_verify_base_dir_rejects_non_dir(tmp_path):
    f = str(tmp_path / "afile")
    with open(f, "w") as fh:
        fh.write("x")
    os.chmod(f, 0o700)
    with pytest.raises(RuntimeError):
        paths.verify_base_dir(f)


def test_verify_base_dir_rejects_missing(tmp_path):
    with pytest.raises(RuntimeError):
        paths.verify_base_dir(str(tmp_path / "nope"))
