# tmux_session.py
"""Synchronous TmuxClient — every tmux call is an argv list run shell=False.

No HTTP, no registry, no path-safety here: this module is a thin, well-typed
port over the `tmux` binary. Liveness is `has-session` ONLY (design risk #11).
Errors are classified transient (retryable) vs authoritative (risk #13) so the
reaper never evicts a live session on a fork hiccup.

NAMED_KEYS is defined here once — every other component imports it from here.
"""
import os
import shlex
import signal
import stat
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

    def _target(self, target):
        # Accept a session name ("t") or a pane id ("%3"); both are valid -t.
        return str(target)

    def capture_pane(self, target):
        """`capture-pane -pN` — the ONLY screen source (FSM strips it).

        -N preserves trailing spaces; without it tmux trims them and the
        composer prompt "❯ " collapses to "❯", which the FSM keys on.
        """
        r = self._run("capture-pane", "-p", "-N", "-t", self._target(target))
        return r.stdout

    def _display(self, target, fmt):
        r = self._run("display-message", "-p", "-t", self._target(target), fmt)
        return (r.stdout or "").strip()

    def alternate_on(self, target):
        """Must be 0 — claude renders inline, not on the alternate screen."""
        v = self._display(target, "#{alternate_on}")
        return int(v or "0")

    def pane_current_command(self, target):
        return self._display(target, "#{pane_current_command}")

    def pane_dead(self, target):
        return self._display(target, "#{pane_dead}") == "1"

    def pane_pid(self, target):
        v = self._display(target, "#{pane_pid}")
        return int(v or "0")

    def tpgid(self, target):
        """Foreground process-GROUP id of the pane (os.getpgid of #{pane_pid}).

        Canonical for the guarded killpg in Task 9 — a real pgid, never a raw
        pid. cleanup-security must not redefine this.
        """
        pid = self.pane_pid(target)
        if pid <= 0:
            return 0
        try:
            return os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            return 0

    def set_option(self, target, name, value):
        # User options (@wcb_*) are stored per-session; survive a bridge restart.
        self._run("set-option", "-t", self._target(target), name, value)

    def get_option(self, target, name):
        """Missing option -> None (a recoverable default, never 'dead', #11).

        An unset @wcb_* user option makes tmux exit non-zero with
        "invalid option: <name>" — that is the *absence* of a value, a
        recoverable default, NOT a death signal. A genuinely gone session
        (non-retryable) is likewise reported as None here; only a transient
        fault re-raises.
        """
        try:
            r = self._run("show-options", "-v", "-t", self._target(target), name)
        except _TmuxError as e:
            if not e.retryable or "invalid option" in str(e).lower():
                return None
            raise
        out = (r.stdout or "").strip()
        return out if out != "" else None

    def send_keys(self, target, *keys):
        """Send named keys ONLY (NAMED_KEYS allowlist). Reject everything else
        so a caller can never smuggle a tmux key-name or shell string here —
        free-form text must go through send_text (#9/#10)."""
        for k in keys:
            if k not in NAMED_KEYS:
                raise ValueError(f"key not in NAMED_KEYS allowlist: {k!r}")
        self._run("send-keys", "-t", self._target(target), *keys)

    def send_text(self, target, text):
        """Type `text` literally: `send-keys -l -t <pane> -- <text>`.
        The `-l` flag + `--` guard mean tmux interprets no key names and the
        shell evaluates nothing (#9)."""
        self._run("send-keys", "-l", "-t", self._target(target), "--", text)

    def send_prompt(self, target, text):
        """Type a (possibly multi-line) prompt the way claude's composer wants:
        each non-empty line via `send-keys -l … --`, an `M-Enter` between lines
        (composer newline, NOT submit), and one final bare `Enter` to submit.
        Calibration-pinned for claude v2.1.156."""
        lines = text.split("\n")
        last = len(lines) - 1
        for i, line in enumerate(lines):
            if line:
                self.send_text(target, line)
            if i < last:
                self.send_keys(target, "M-Enter")
        self.send_keys(target, "Enter")

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

    @staticmethod
    def _safe_log_name(log_path):
        # '%' is special in pipe-pane's command template — never let it reach
        # the shell sink unescaped; the registry already minted a confined path.
        return log_path.replace("%", "pct")

    def pane_pipe(self, target):
        """1 if a pipe is currently armed on the pane, else 0 (#12)."""
        v = self._display(target, "#{pane_pipe}")
        return int(v or "0")

    def pipe_pane_on(self, target, log_path):
        """Arm pane retention to `log_path`, symlink-safe and re-arm idempotent.

        - Create the log O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW 0600 (refuses a
          pre-existing symlink → no /etc/x clobber, no symlink sink, #8).
        - Disable any existing pipe FIRST (query #{pane_pipe}; argumentless
          pipe-pane) — the `cat >>` form does NOT toggle off, so re-arming
          would otherwise leak a second cat/sh (#12).
        - shlex.quote the full path into the `cat >>` sink command.
        """
        safe = self._safe_log_name(log_path)
        try:
            fd = os.open(
                safe,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
            os.close(fd)
        except FileExistsError:
            # Already created by a prior arm — verify it is a regular file we
            # own and is not a symlink, then reuse it.
            st = os.lstat(safe)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise OSError(f"refusing non-regular log path: {safe}")
            if st.st_uid != os.getuid():
                raise OSError(f"refusing log path not owned by euid: {safe}")
        # Disable-first so re-arming never stacks a second sink (#12).
        if self.pane_pipe(target) == 1:
            self._run("pipe-pane", "-t", self._target(target))
        sink = "cat >> " + shlex.quote(safe)
        self._run("pipe-pane", "-o", "-t", self._target(target), sink)

    def pipe_pane_off(self, target):
        """Disable retention (argumentless pipe-pane toggles it off)."""
        if self.pane_pipe(target) == 1:
            self._run("pipe-pane", "-t", self._target(target))

    def interrupt(self, target, shell_pid, _tpgid_override=None):
        """Guarded SIGINT to the pane's foreground process group, with a C-c
        key fallback.

        Only acts when tpgid > 0 AND tpgid != shell_pid — never SIGINT the
        session's own shell (that would tear the session down). Attempts
        killpg(tpgid, SIGINT); if the group is already gone or not ours, falls
        back to a `send_keys(target, "C-c")` so a TUI-level interrupt still
        reaches claude. Returns True if it acted, False if guarded out.
        (`_tpgid_override` is a test seam; production callers omit it.)
        """
        pgid = _tpgid_override if _tpgid_override is not None else self.tpgid(target)
        if pgid <= 0 or pgid == shell_pid:
            return False
        try:
            os.killpg(pgid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            # Group gone or not ours — fall back to a TUI-level C-c.
            self.send_keys(target, "C-c")
        return True
