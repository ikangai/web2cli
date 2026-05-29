# tmux_session.py
"""Synchronous TmuxClient — every tmux call is an argv list run shell=False.

No HTTP, no registry, no path-safety here: this module is a thin, well-typed
port over the `tmux` binary. Liveness is `has-session` ONLY (design risk #11).
Errors are classified transient (retryable) vs authoritative (risk #13) so the
reaper never evicts a live session on a fork hiccup.

NAMED_KEYS is defined here once — every other component imports it from here.
"""
import os
import signal
import subprocess

# Liveness/authoritative phrases tmux emits to stderr when a server/session is
# genuinely gone. Anything else (EAGAIN/ENOMEM fork failure, EINTR, a timeout)
# is treated as transient.
_GONE_PHRASES = (
    "no server running",
    "no current session",
    "session not found",
    "can't find session",
    "error connecting to",   # stale/absent socket
)

NAMED_KEYS = frozenset({
    "Enter", "Escape", "Up", "Down", "Left", "Right",
    "M-Enter", "C-c", "BSpace", "Tab", "Space",
})


class _TmuxError(Exception):
    def __init__(self, msg, *, retryable):
        super().__init__(msg)
        self.retryable = retryable


class TmuxClient:
    def __init__(self, socket, tmux_bin="tmux"):
        self.socket = socket
        self.tmux_bin = tmux_bin

    def _argv(self, *args):
        return [self.tmux_bin, "-L", self.socket, *args]

    def _run(self, *args, timeout=10.0):
        """Run a tmux subcommand. Return CompletedProcess on rc==0.

        Raise _TmuxError(retryable=False) when stderr names a gone server/
        session or the binary is missing; _TmuxError(retryable=True) on a
        timeout or transient OSError.
        """
        try:
            r = subprocess.run(
                self._argv(*args),
                capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError as e:
            # The tmux binary itself is absent — authoritative (-> 503 upstream).
            raise _TmuxError(f"tmux binary not found: {e}", retryable=False)
        except subprocess.TimeoutExpired as e:
            raise _TmuxError(f"tmux timed out: {e}", retryable=True)
        except OSError as e:
            # EAGAIN/ENOMEM fork failure, EINTR — transient.
            raise _TmuxError(f"tmux spawn failed: {e}", retryable=True)
        if r.returncode != 0:
            stderr = (r.stderr or "").strip()
            low = stderr.lower()
            if any(p in low for p in _GONE_PHRASES):
                raise _TmuxError(stderr or "session gone", retryable=False)
            raise _TmuxError(stderr or f"tmux exited {r.returncode}",
                             retryable=True)
        return r

    def new_session(self, name, cwd, cols, rows, argv):
        """Create a detached session with claude (or the fake) as the pane cmd.

        Mirrors: tmux -L <sock> -f /dev/null new-session -d -s <name>
                 -c <cwd> -x <cols> -y <rows> -- <argv...>
        The pane command IS `argv` (the registry injects claude_argv or the
        fake_claude_argv fixture) — the readiness path must NOT re-type a
        launch line. Returns the pane id (%N).
        """
        self._run(
            "-f", "/dev/null", "new-session", "-d", "-s", name,
            "-c", cwd, "-x", str(cols), "-y", str(rows),
            "--", *argv,
        )
        return self.pane_id(name)

    def has_session(self, name):
        """The ONLY truth source for liveness. Never raises for 'gone'."""
        try:
            self._run("has-session", "-t", name)
            return True
        except _TmuxError as e:
            if not e.retryable:
                return False
            raise

    def pane_id(self, name):
        r = self._run("list-panes", "-t", name, "-F", "#{pane_id}")
        return (r.stdout or "").strip().splitlines()[0]

    def socket_path(self):
        """Absolute path of the -L socket (for defensive socket-clear / unlink,
        risk #11). Server must be up."""
        r = self._run("display-message", "-p", "#{socket_path}")
        return (r.stdout or "").strip()

    def kill_server(self):
        try:
            self._run("kill-server")
        except _TmuxError as e:
            # Killing an already-dead server is a no-op, not a failure.
            if e.retryable:
                raise
