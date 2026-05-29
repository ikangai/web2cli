import os
import pathlib
import shutil
import subprocess

import pytest

TMUX = shutil.which("tmux")
requires_tmux = pytest.mark.skipif(TMUX is None, reason="tmux binary not found")

FAKE = pathlib.Path(__file__).parent / "fake_claude.sh"
CAPTURES = pathlib.Path(__file__).parent / "fixtures" / "captures"


@pytest.fixture
def tmp_base(tmp_path):
    """A 0700 rendezvous base dir owned by euid (mimics verify_base_dir)."""
    d = tmp_path / "rendezvous"
    d.mkdir(mode=0o700)
    # mkdir mode is masked by umask; force the exact bits verify_base_dir wants.
    os.chmod(d, 0o700)
    return d


@pytest.fixture
def capture():
    """Return a reader: capture("composer_ready.txt") -> str (the sliced screen)."""
    return lambda name: (CAPTURES / name).read_text()


@pytest.fixture
def fake_socket():
    """Unique -L socket name per test; kill-server in teardown so no leaks."""
    sock = "wcbtest_" + os.urandom(4).hex()
    yield sock
    if TMUX is not None:
        subprocess.run(
            [TMUX, "-L", sock, "kill-server"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )


@pytest.fixture
def fake_claude_argv():
    """argv that runs the fake claude mimic instead of the real binary."""
    return ["bash", str(FAKE)]
