"""In-memory reconstructable cache over `tmux list-sessions`.

The registry is NEVER the source of truth for liveness — `has_session` is.
Two-level locking: a module-level structural Lock guards dict mutation ONLY
(never wraps tmux I/O or a stream loop); a per-_Session turn Lock serializes
turns. See design (1) "Registry & locking".
"""
import hmac
import os
import re
import shutil
import stat
import threading
import time

import paths
from fsm import FOOTER_IDLE, TRUST_PROMPT, classify, footer_of, strip_screen
from tmux_session import TmuxClient, _TmuxError

MAX_SESSIONS = 8                # design (4): max concurrent-session cap

READY_TIMEOUT = 30.0
READY_POLL = 0.25

LIST_CACHE_TTL = 1.0            # design (4): brief /list cache (sec)


class _MaxSessionsReached(Exception):
    pass


class _AlternateScreenError(Exception):
    pass


class _ReadinessTimeout(Exception):
    pass


class _NotFound(Exception):
    pass


class _RendezvousRedirect(Exception):
    pass


class _SessionBusy(Exception):
    pass


def _ensure_session_dir(base, sid, nonce):
    """Create the per-session rendezvous dir 0700, confined under base.

    A real nonce is secrets.token_hex(8) = 16 hex; reject a malformed/tampered
    nonce with ValueError (deterministic gate), and keep assert_confined as the
    hard guard against any path-bound escape (risk #6/#14)."""
    if not re.fullmatch(r"[0-9a-f]{16}", nonce or ""):
        raise ValueError("invalid rendezvous nonce: %r" % (nonce,))
    d = paths.session_dir(base, sid, nonce)
    paths.assert_confined(d, base)          # returns None; raises PathSafetyError
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)                       # defeat umask
    return d


_ENV_RE = re.compile(r"\Aenv\.[0-9a-f-]{36}\.json\Z")
_DOC_RE = re.compile(r"\Adoc\.[0-9a-f-]{36}\.html(\.part)?\Z")


def _sweep_stale(session_dir_path, base, keep_doc_uuid=None):
    """Unlink ALL leftover env.*.json and old doc.*.html (verified-regular,
    O_NOFOLLOW). Symlinks are removed via os.unlink without following.
    Never select by recency. The current turn's doc (keep_doc_uuid) survives.
    """
    paths.assert_confined(session_dir_path, base)
    try:
        names = os.listdir(session_dir_path)
    except FileNotFoundError:
        return
    for name in names:
        is_env = _ENV_RE.match(name)
        is_doc = _DOC_RE.match(name)
        if not (is_env or is_doc):
            continue
        if is_doc and keep_doc_uuid and name == f"doc.{keep_doc_uuid}.html":
            continue
        # RECONCILED: `name` is a bare filename (the _ENV_RE/_DOC_RE anchors
        # forbid any '/' or '..'), so its literal containment in the
        # already-confined session dir is guaranteed. We must NOT realpath-
        # resolve `full` per-entry: assert_confined would follow a hostile
        # symlink to its target outside `base` and refuse the unlink — but the
        # whole point of the sweep is to remove that symlink *entry* (never its
        # target). os.unlink does not follow the final symlink, so removing the
        # dangling/escaping link is safe and its target is untouched.
        full = os.path.join(session_dir_path, name)
        try:
            os.unlink(full)               # does not follow the final symlink
        except FileNotFoundError:
            pass


class DocNotStaged(Exception):
    """Stream requested before the rwa staged the current-turn doc -> 409.

    On reconstruct the staged flag is lost; the dispatcher (cleanup-security
    component) maps this to 409 {"error":"doc_not_staged"} (design §4).
    """


def mark_doc_staged(session) -> None:
    session.doc_staged = True


def assert_doc_staged(session) -> None:
    if not getattr(session, "doc_staged", False):
        raise DocNotStaged("rwa must re-stage the current-turn doc")


def stage_turn(tmux, base, session_dir_path, turn_uuid, persisted_bytes,
               *, session=None) -> str:
    """Held-lock turn staging: sweep leftovers, inject sentinel, atomically
    put doc.<turn_uuid>.html, unlink prior env.<turn_uuid>.json, mark busy.

    Called by turn-protocol-fsm's _run_turn_locked, which does send-keys AFTER
    this returns (rename happens-before the prompt). Returns the staged doc
    path. tmux is a TmuxClient (or stub) exposing set_option(name, value).
    If `session` is given, flips its doc_staged flag so the post-reconstruct
    409 precondition (assert_doc_staged) passes for this turn.
    """
    paths.validate_turn_uuid(turn_uuid)
    _sweep_stale(session_dir_path, base, keep_doc_uuid=None)
    staged = paths.inject_gen_sentinel(persisted_bytes, turn_uuid)
    doc = paths.put_doc(session_dir_path, base, turn_uuid, staged)
    # belt-and-suspenders: ensure no same-uuid env predates this turn
    env = paths.env_path(session_dir_path, turn_uuid)
    paths.assert_confined(env, base)
    try:
        os.unlink(env)
    except FileNotFoundError:
        pass
    tmux.set_option("@wcb_turn", turn_uuid)
    if session is not None:
        mark_doc_staged(session)
    return doc


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
        # design §4: a stream after reconstruct is refused (409 doc_not_staged)
        # until the rwa re-stages this turn's doc. Both fresh-create and
        # reconstruct start unstaged; stage_turn(session=s) flips it.
        self.doc_staged = False
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
        self._list_cache = None                 # (built_at, rows)

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

    def list_sessions(self):
        """Gather tmux facts first (lock FREE), then under the structural lock
        apply only additive reads. state=busy whenever the turn lock is held;
        NEVER evict here (design (1)). Result cached briefly (design (4))."""
        cache = self._list_cache
        if cache is not None and (time.monotonic() - cache[0]) < LIST_CACHE_TTL:
            return cache[1]

        sessions = self._snapshot()             # copy under lock, release
        rows = []
        for s in sessions:
            # tmux I/O happens with the structural lock FREE.
            try:
                alive = s.tmux.has_session("t")
            except Exception:
                alive = False

            if s.turn_lock.locked():
                state = "busy"
            elif s.status == "RECONSTRUCTING":
                state = "reconstructing"
            elif s.is_busy():                   # @wcb_turn OR FSM != idle
                state = "busy"
            else:
                state = "idle"

            try:
                on_disk = os.path.getsize(s.log_path) if s.log_path else 0
            except OSError:
                on_disk = 0
            # CRITIQUE-FIX: report bytes in the global (rotation-adjusted) space.
            log_bytes = on_disk + s.log_offset_base

            rows.append({
                "session_id": s.sid,
                "state": state,
                "created_at": s.created_at,
                "log_bytes": log_bytes,
                "alive": bool(alive),
            })
        self._list_cache = (time.monotonic(), rows)
        return rows

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

        pane = tmux.new_session("t", rcwd, cols, rows)   # shell; claude typed in _await_ready

        # Verify the inline-capture invariant ONCE (design (1), calibration):
        # claude renders inline (alternate_on must be 0) or capture-pane -p
        # is not a valid view of the TUI.
        if tmux.alternate_on(pane) != 0:
            try:
                tmux.kill_server()
            except Exception:
                pass
            raise _AlternateScreenError("alternate_on != 0; aborting create")

        # Arm pipe-pane retention to a private 0600 log. The disable-first
        # pipe_pane_on definition is owned by the tmux-client component
        # (re-pipe idempotency / pane_pipe()==1 tested there); here we arm it.
        tmux.pipe_pane_on(pane, log_path)

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

        try:
            self._await_ready(s, claude_argv)
        except Exception:
            try:
                tmux.kill_server()
            except Exception:
                pass
            raise

        with self._lock:
            self._sessions[sid] = s
        s.status = "READY"
        s.ready.set()
        return s

    def _await_ready(self, s, claude_argv):
        """Type the launch argv into the shell pane, then a POSITIVE composer-
        ready probe (never quiescence, risk #2). Detect+answer the workspace-
        trust prompt with ['1','Enter'] (bypassPermissions does NOT suppress it)."""
        s.tmux.send_text(s.pane, " ".join(claude_argv))
        s.tmux.send_keys(s.pane, "Enter")

        deadline = time.monotonic() + READY_TIMEOUT
        trust_answered = False
        while time.monotonic() < deadline:
            screen = strip_screen(s.tmux.capture_pane(s.pane))
            footer = footer_of(screen)
            state, meta = classify(
                screen, footer, env_present=False,
                prev_timer=None, composer_seen=s.composer_seen,
            )
            if state == "awaiting_input" and meta.get("kind") == "trust" \
                    and not trust_answered:
                # '1' is free-form text (not a NAMED_KEY) -> send_text; the bare
                # Enter that confirms the menu choice goes through send_keys.
                s.tmux.send_text(s.pane, "1")
                s.tmux.send_keys(s.pane, "Enter")
                trust_answered = True
                time.sleep(READY_POLL)
                continue
            if FOOTER_IDLE in screen and "❯ 1." not in screen \
                    and TRUST_PROMPT not in screen:
                s.composer_seen = True
                return
            time.sleep(READY_POLL)
        raise _ReadinessTimeout("composer not ready within %.0fs" % READY_TIMEOUT)

    def get_or_reconstruct(self, sid):
        """Placeholder + Event pattern (design (1), kills the dual-lock race)."""
        paths.validate_session_id(sid)
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                s = _Session(
                    sid=sid, cap=None, nonce=None,
                    socket=("wcb_" + sid), pane=None,
                    cwd=None, rendezvous_dir=None, log_path=None,
                    created_at=time.time(), tmux=TmuxClient("wcb_" + sid),
                )
                self._sessions[sid] = s
                winner = True
                s._claimed = True
            elif s.status == "RECONSTRUCTING" and not s._claimed:
                s._claimed = True
                winner = True
            else:
                winner = False

        if winner and s.status == "RECONSTRUCTING":
            try:
                self._hydrate(s)
                s.status = "READY"
            except Exception:
                with self._lock:
                    if self._sessions.get(sid) is s:
                        del self._sessions[sid]
                s.ready.set()
                raise
            finally:
                s.ready.set()
            return s

        if s.status == "READY":
            return s
        s.ready.wait(timeout=READY_TIMEOUT)
        if self._sessions.get(sid) is not s or s.status != "READY":
            raise _NotFound(sid)
        return s

    def _hydrate(self, s):
        """Rebuild volatile fields from tmux facts. Liveness is has-session
        ONLY; missing @wcb_* options are recoverable defaults (risk #11)."""
        if not s.tmux.has_session("t"):
            raise _NotFound(s.sid)
        s.pane = s.tmux.pane_id("t")
        created = s.tmux.get_option("@wcb_created")
        nonce = s.tmux.get_option("@wcb_nonce")
        if created:
            try:
                s.created_at = float(created)
            except ValueError:
                pass
        if nonce:
            # RECONCILED risk #14: a real nonce is secrets.token_hex(8) = 16 hex.
            # Reject a tampered/malformed nonce, and cross-check confinement.
            if not re.fullmatch(r"[0-9a-f]{16}", nonce):
                raise _RendezvousRedirect(s.sid)
            base = self._base
            rdir = paths.session_dir(base, s.sid, nonce)
            try:
                paths.assert_confined(rdir, base)   # returns None; raises on escape
            except paths.PathSafetyError:
                raise _RendezvousRedirect(s.sid)
            s.nonce = nonce
            s.rendezvous_dir = rdir
            s.log_path = os.path.join(rdir, "log")

    def delete(self, sid, cap):
        """Confined teardown: cap match, refuse if busy, kill-server, unlink the
        socket, rm -rf the dir ONLY if it realpath-confines under base."""
        paths.validate_session_id(sid)
        with self._lock:
            s = self._sessions.get(sid)
        if s is None:
            raise _NotFound(sid)

        if not s.cap or not isinstance(cap, str):
            raise PermissionError("cap required")
        try:
            if not hmac.compare_digest(s.cap, cap):
                raise PermissionError("cap mismatch")
        except (TypeError, ValueError):
            raise PermissionError("cap mismatch")

        if s.turn_lock.locked():
            raise _SessionBusy(sid)

        try:
            s.tmux.kill_server()
        except Exception:
            pass
        sock_path = self._socket_path(s.socket)        # reconciliation B
        if sock_path:
            try:
                os.unlink(sock_path)
            except OSError:
                pass

        if s.rendezvous_dir:                            # reconciliation A
            try:
                paths.assert_confined(s.rendezvous_dir, self._base)
            except paths.PathSafetyError:
                raise PermissionError(
                    "rendezvous dir not confined: %s" % s.rendezvous_dir)
            shutil.rmtree(s.rendezvous_dir, ignore_errors=True)

        with self._lock:
            if self._sessions.get(sid) is s:
                del self._sessions[sid]
        self._list_cache = None                         # invalidate /list cache

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
