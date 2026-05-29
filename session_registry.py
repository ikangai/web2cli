"""In-memory reconstructable cache over `tmux list-sessions`.

The registry is NEVER the source of truth for liveness — `has_session` is.
Two-level locking: a module-level structural Lock guards dict mutation ONLY
(never wraps tmux I/O or a stream loop); a per-_Session turn Lock serializes
turns. See design (1) "Registry & locking".
"""
import os
import stat
import threading
import time

import paths
from fsm import classify, footer_of, strip_screen
from tmux_session import TmuxClient, _TmuxError

MAX_SESSIONS = 8                # design (4): max concurrent-session cap


class _MaxSessionsReached(Exception):
    pass


class _Session:
    def __init__(self, *, sid, cap, nonce, socket, pane, cwd,
                 rendezvous_dir, log_path, created_at, tmux):
        self.sid = sid
        self.cap = cap
        self.nonce = nonce
        self.socket = socket
        self.pane = pane
        self.cwd = cwd
        self.rendezvous_dir = rendezvous_dir
        self.log_path = log_path
        self.created_at = created_at
        self.tmux = tmux
        self.turn_lock = threading.Lock()
        self.ready = threading.Event()
        self.status = "RECONSTRUCTING"          # -> "READY" after hydrate
        # CRITIQUE-FIX: consolidated field set — siblings (reaper, turn,
        # replay-offset) rely on these existing from creation, never bolted on.
        self.shell_pid = None                   # cleanup-security interrupt/reaper
        self.composer_seen = False              # FSM dead-discriminator latch
        self._gone_strikes = 0                  # reaper multi-poll corroboration
        self.log_offset_base = 0                # replay offset across rotation
        self._claimed = False                   # get_or_reconstruct hydrate guard
        # Test seam: overridden in tests; real classify path below.
        self._classify_state = None

    def _state(self):
        """Best-effort FSM state from a fresh capture (no payload read)."""
        if self._classify_state is not None:
            return self._classify_state()
        screen = strip_screen(self.tmux.capture_pane(self.pane))
        # CRITIQUE-FIX: pinned classify signature — pass composer_seen always.
        state, _meta = classify(
            screen, footer_of(screen), env_present=False,
            prev_timer=None, composer_seen=self.composer_seen,
        )
        return state

    def is_busy(self):
        """busy = turn_lock held OR @wcb_turn set OR FSM != idle.

        Durable across restart via @wcb_turn (design risk #5).
        """
        if self.turn_lock.locked():
            return True
        turn = self.tmux.get_option("@wcb_turn")
        if turn:
            return True
        state = self._state()
        return state not in ("idle", "idle_no_envelope")


class _Registry:
    def __init__(self, base=None):
        self._lock = threading.Lock()           # STRUCTURAL ONLY
        self._sessions = {}                      # sid -> _Session
        # CRITIQUE-FIX: pinned constructor — explicit base or paths.base_dir().
        self._base = base if base is not None else paths.base_dir()

    def _snapshot(self):
        """Copy the dict under the structural lock; release before any I/O."""
        with self._lock:
            return list(self._sessions.values())

    def _alive_ids(self, sessions):
        """tmux liveness probe OUTSIDE the structural lock (design (1))."""
        alive = []
        for s in sessions:
            try:
                if s.tmux.has_session("t"):
                    alive.append(s.sid)
            except Exception:
                pass
        return alive

    def create(self, *, cwd, cols, rows, claude_argv, socket_override=None):
        # 0) confine cwd: must be an existing dir, realpath-resolved.
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("cwd must be a non-empty string")
        rcwd = os.path.realpath(cwd)
        if not os.path.isdir(rcwd):
            raise NotADirectoryError(cwd)

        # cap concurrent sessions (design (4) -> 429)
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                raise _MaxSessionsReached(
                    f"max {MAX_SESSIONS} concurrent sessions")

        base = self._base
        paths.verify_base_dir(base)        # S_ISDIR & !LNK & uid==euid & 0700

        sid = paths.mint_session_id()
        cap = paths.mint_cap()
        nonce = paths.mint_nonce()
        socket = socket_override or ("wcb_" + sid)
        rdir = paths.session_dir(base, sid, nonce)
        paths.assert_confined(rdir, base)

        # 0700 dir + explicit chmod (defeat umask) + 0600 cap file.
        os.makedirs(rdir, mode=0o700, exist_ok=False)
        os.chmod(rdir, 0o700)
        cap_path = os.path.join(rdir, "cap")
        fd = os.open(cap_path,
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, cap.encode("ascii"))
        finally:
            os.close(fd)

        log_path = os.path.join(rdir, "log")
        tmux = TmuxClient(socket)

        # Defensive socket-path clear (design (1), risk #11): a leftover
        # NON-socket file permanently bricks new-session; a stale socket is
        # cleared iff has-session fails.
        self._clear_stale_socket(tmux, socket)

        # RECONCILED: pass claude_argv (the pane command IS argv — canonical
        # tmux-client contract). Real argv in prod, fake_claude_argv in tests.
        pane = tmux.new_session("t", rcwd, cols, rows, claude_argv)

        s = _Session(
            sid=sid, cap=cap, nonce=nonce, socket=socket, pane=pane,
            cwd=rcwd, rendezvous_dir=rdir, log_path=log_path,
            created_at=time.time(), tmux=tmux,
        )
        # persist durable options (survive a bridge restart, no Python state).
        # RECONCILED: TmuxClient.set_option is (target, name, value) — pass the
        # session target "t" (the plan's 2-arg form was authoring drift).
        tmux.set_option("t", "@wcb_created", str(int(s.created_at)))
        tmux.set_option("t", "@wcb_nonce", nonce)

        with self._lock:
            self._sessions[sid] = s
        s.status = "READY"
        s.ready.set()
        return s

    @staticmethod
    def _socket_path(socket):
        """Canonical `-L <socket>` path WITHOUT a live server.

        RECONCILED: TmuxClient.socket_path() asks a *running* server via
        display-message, but the defensive clear must run BEFORE the server
        exists (chicken-and-egg) — so derive the same path tmux itself uses:
        $TMUX_TMPDIR (or /tmp) / tmux-<uid> / <socket>. This is the exact
        layout the test teardown unlinks, so no leak slips through.
        """
        sock_dir = os.environ.get("TMUX_TMPDIR") or "/tmp"
        return os.path.join(sock_dir, "tmux-%d" % os.getuid(), socket)

    def _clear_stale_socket(self, tmux, socket):
        """Unlink a leftover non-socket file; unlink a dead socket (risk #11)."""
        sock_path = self._socket_path(socket)
        if not sock_path:
            return
        try:
            st = os.lstat(sock_path)
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(st.st_mode):
            os.unlink(sock_path)            # non-socket file -> unconditional clear
            return
        try:
            if not tmux.has_session("t"):
                os.unlink(sock_path)
        except _TmuxError:
            try:
                os.unlink(sock_path)
            except OSError:
                pass


REGISTRY = _Registry()
