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
import uuid

import paths
from fsm import FOOTER_IDLE, TRUST_PROMPT, classify, footer_of, strip_screen
from tmux_session import TmuxClient, _TmuxError

MAX_SESSIONS = 8                # design (4): max concurrent-session cap

READY_TIMEOUT = 30.0
READY_POLL = 0.25

LIST_CACHE_TTL = 1.0            # design (4): brief /list cache (sec)

GRACE_SECONDS = 30.0            # reaper: min age before a session is reapable
REAP_STRIKES = 2               # reaper: consecutive confirmed-gone passes to evict


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


class SessionBusy(Exception):
    """A turn was requested while this session's turn lock is held -> 409.

    Public (the HTTP dispatcher maps it to 409 busy). Distinct from the
    internal _SessionBusy raised by delete() when a teardown races a turn;
    both mean "the session is mid-turn", but the turn path is the caller-
    facing one the dispatcher keys on.
    """


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
    path. tmux is a TmuxClient (or stub) exposing set_option(target, name, value).
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
    tmux.set_option("t", "@wcb_turn", turn_uuid)
    if session is not None:
        mark_doc_staged(session)
    return doc


def build_turn_prompt(instruction, doc_path_abs, env_path_abs, turn_uuid) -> str:
    """The per-turn PRODUCTION prompt. claude READS doc.<uuid>.html (fresh, not
    memory), produces an edit envelope, and WRITES env.<uuid>.json via a
    .part->rename completion edge. The gen sentinel + turn_uuid must be echoed
    verbatim so the bridge can reject a stale/mis-targeted write.

    NOTE: the fake-claude `WRITE {env} {uuid}` template used by the test harness
    is a fixture only; production uses THIS prompt.
    """
    part = env_path_abs + ".part"
    # Canonicalize the caller's instruction to \n (no literal \r leaks into the
    # prompt) and indent EVERY line so a multi-line instruction stays an indented
    # block under step 3. This also closes a protocol-forgery edge: a
    # continuation line can never reach column 0, so it can never masquerade as
    # the column-0 `WRITE <env> <uuid>` machine handshake (review finding #11).
    instruction = str(instruction).replace("\r\n", "\n").replace("\r", "\n")
    instruction = instruction.replace("\n", "\n   ")
    return (
        "You are editing an HTML document through a file rendezvous.\n\n"
        f"1. Read the CURRENT document now from this exact absolute path "
        f"(do not rely on memory of any earlier version):\n   {doc_path_abs}\n\n"
        "2. The document contains a comment of the form "
        f"`<!-- rwa:gen {turn_uuid} -->`. Copy that uuid verbatim.\n\n"
        f"3. Apply this instruction:\n   {instruction}\n\n"
        "4. Produce a single edit envelope. It MUST be one of:\n"
        '   {"tool":"apply_edits","envelope":{"version":"rwa-edit/1",'
        '"edits":[{"find":"...","replace":"..."}]}}\n'
        '   {"tool":"replace_document","envelope":{"version":"rwa-edit/1",'
        '"doc":"...","reason":"..."}}\n'
        "   Each `find` string MUST be copied byte-exact from the document you "
        "just read — do not reflow, re-indent, or re-quote it.\n\n"
        "5. Wrap the envelope object and add two top-level fields copied "
        "verbatim:\n"
        f'   "turn_uuid": "{turn_uuid}"\n'
        f'   "gen": "{turn_uuid}"   (the uuid from the rwa:gen comment)\n\n'
        "6. Write that JSON object using your Write tool to the SCRATCH path "
        f"FIRST:\n   {part}\n"
        "   then RENAME (move) it to the FINAL path:\n"
        f"   {env_path_abs}\n"
        "   The rename is the completion signal; do not write the final path "
        "directly.\n\n"
        "7. After the rename succeeds, reply with only the word DONE.\n"
        "\n"
        # Machine handshake on the FINAL line: name the env file + turn_uuid so
        # an automated driver can locate the rendezvous target without parsing
        # prose. It MUST stay last and carry NO trailing newline so send_prompt
        # delivers it as one clean line (every earlier line gets an M-Enter
        # composer-newline; only the last is terminated by the bare submit
        # Enter). The canonical test fake (tests/fake_claude.sh) keys on this
        # `WRITE <env> <uuid>` line; real claude reads it as a terse restatement
        # of the path it was already told to write in step 6.
        f"WRITE {env_path_abs} {turn_uuid}"
    )


def await_envelope(session_dir_path, base, turn_uuid, send_time,
                   *, deadline, stable_ms=300, poll_ms=50) -> bytes:
    """Poll for the renamed FINAL env.<turn_uuid>.json. Accept only a file
    whose mtime > send_time and whose size+mtime are stable for >= stable_ms.
    Then read it byte-exact via paths.read_envelope_bytes. Never reads a .part
    or a stale (older) file.

    Raises paths.EnvelopeNotWritten if none appears before `deadline`
    (time.monotonic seconds).
    """
    env = paths.env_path(session_dir_path, turn_uuid)
    paths.assert_confined(env, base)
    stable_s = stable_ms / 1000.0
    poll_s = poll_ms / 1000.0
    last_sig = None
    stable_since = None
    while time.monotonic() < deadline:
        try:
            st = os.lstat(env)
        except FileNotFoundError:
            last_sig = None
            stable_since = None
            time.sleep(poll_s)
            continue
        # freshness: written by THIS turn (after we sent the prompt)
        if st.st_mtime <= send_time:
            time.sleep(poll_s)
            continue
        sig = (st.st_size, st.st_mtime)
        if sig != last_sig:
            last_sig = sig
            stable_since = time.monotonic()
            time.sleep(poll_s)
            continue
        if time.monotonic() - stable_since >= stable_s:
            return paths.read_envelope_bytes(env, turn_uuid)
        time.sleep(poll_s)
    raise paths.EnvelopeNotWritten(env)


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
        turn = self.tmux.get_option("t", "@wcb_turn")
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

    # --- turn protocol (held-lock) -----------------------------------------
    # Completion is the FILE EDGE (rendezvous-docsync await_envelope), never
    # screen quiescence — claude's TUI has multi-second thinking gaps that look
    # idle (design §4). The screen is read only for awaiting_input / dead.
    _TURN_ACQUIRE_TIMEOUT = 2.0    # bounded acquire => observable 409 (not queue)
    _POLL_SLICE_S = 0.4            # per-iteration file-edge budget (>= stable_ms)
    _AWAIT_STABLE_MS = 150         # env size+mtime must hold this long before read
    _AWAIT_POLL_MS = 50
    _DEAD_QUIESCENT_S = 1.0        # a dead candidate must persist+corroborate

    def mint_turn_uuid(self):
        """Bridge-minted per-turn uuid (risk #7) — never caller-supplied."""
        return str(uuid.uuid4())

    def run_turn(self, sess, **kw):
        """Acquire the bounded per-session turn lock, then delegate.

        The BOUNDED acquire is what makes a concurrent turn observably 409: if
        the lock is already held we give up after _TURN_ACQUIRE_TIMEOUT and
        raise SessionBusy rather than queueing behind the in-flight turn.
        Released in finally so a turn ending never leaves the session wedged
        (and never kills it)."""
        if not sess.turn_lock.acquire(timeout=self._TURN_ACQUIRE_TIMEOUT):
            raise SessionBusy(sess.sid)
        try:
            return self.run_turn_locked(sess, **kw)
        finally:
            sess.turn_lock.release()

    def run_turn_locked(self, sess, *, instruction, doc, turn_uuid, timeout,
                        on_event=None):
        """Assumes the turn lock is ALREADY held. The HTTP layer (cleanup-
        security) acquires it BEFORE send_response(200) so a second turn sees
        409 while this one streams, then reaches this method directly."""
        return self._run_turn_locked(
            sess, instruction=instruction, doc=doc, turn_uuid=turn_uuid,
            timeout=timeout, on_event=on_event)

    def _run_turn_locked(self, sess, *, instruction, doc, turn_uuid, timeout,
                         on_event):
        target = sess.pane
        base = self._base
        # The caller's doc may arrive CRLF; canonicalize to \n here (the
        # producer's job, matching canonLF on the rwa side) before staging —
        # put_doc/inject_gen_sentinel REJECT any \r. This is pure and cannot set
        # @wcb_turn, so it stays outside the try below.
        doc_bytes = doc.encode("utf-8") if isinstance(doc, str) else bytes(doc)
        doc_bytes = doc_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

        # stage_turn SETS @wcb_turn (the durable busy flag). Keep it inside the
        # try so the finally clears @wcb_turn on EVERY path, even if staging or
        # prompt construction throws after the flag was set (single-scope
        # set/clear — review finding #2). rendezvous-docsync owns the staging:
        # stale-env sweep + gen-sentinel inject + atomic O_NOFOLLOW doc write +
        # doc_staged flip for the post-reconstruct 409 precondition.
        try:
            doc_path = stage_turn(sess.tmux, base, sess.rendezvous_dir,
                                  turn_uuid, doc_bytes, session=sess)
            env_path = paths.env_path(sess.rendezvous_dir, turn_uuid)
            send_time = time.time()    # set BEFORE send so a fresh file mtime > it
            if not instruction:
                # No instruction dispatched -> no work, hence no thinking-gap
                # hazard: the turn is a trivial no-op. stage_turn already swept
                # any stale env, so there is nothing fresh to read. Return at
                # once rather than block the whole timeout.
                return self._turn_result("idle_no_envelope", "idle_no_envelope",
                                         sess, envelope_bytes=None)
            prompt = build_turn_prompt(instruction, doc_path, env_path,
                                       turn_uuid)
            sess.tmux.send_prompt(target, prompt)
            return self._await_completion(
                sess, env_path=env_path, turn_uuid=turn_uuid,
                send_time=send_time, timeout=timeout, target=target,
                on_event=on_event)
        finally:
            # Clear the durable busy flag. target-first signature (the option-
            # drift fix): TmuxClient.set_option is (target, name, value).
            try:
                sess.tmux.set_option("t", "@wcb_turn", "")
            except Exception:
                pass

    def _turn_result(self, reason, state, sess, *, envelope_bytes, **extra):
        """Uniform turn outcome dict. `alive` defaults to a has-session probe;
        callers override it via extra (e.g. dead => alive=False)."""
        try:
            alive = sess.tmux.has_session("t")
        except Exception:
            alive = False
        r = {"reason": reason, "state": state, "log_offset": 0,
             "alive": alive, "envelope_bytes": envelope_bytes}
        r.update(extra)
        return r

    def _await_completion(self, sess, *, env_path, turn_uuid, send_time,
                          timeout, target, on_event):
        """Interleave the authoritative FILE edge with a screen read.

        Each iteration first asks rendezvous-docsync's await_envelope for a
        fresh (.part->renamed, mtime>send_time, size+mtime-stable) envelope and,
        on success, returns it byte-exact + gen-sentinel-checked (await_envelope
        -> read_envelope_bytes -> verify_envelope_sentinel, risk #4). Only
        BETWEEN those bounded polls do we read the screen, and only to surface
        awaiting_input or a corroborated dead pane. If the budget elapses with
        no fresh file we report idle_no_envelope — we NEVER read a stale file
        and NEVER treat an idle-looking screen as completion (design §4)."""
        deadline = time.monotonic() + timeout
        state = "thinking"
        prev_timer = None
        dead_since = None
        while time.monotonic() < deadline:
            # 1) authoritative file edge, bounded so we still read the screen.
            slice_deadline = min(deadline,
                                 time.monotonic() + self._POLL_SLICE_S)
            try:
                raw = await_envelope(
                    sess.rendezvous_dir, self._base, turn_uuid, send_time,
                    deadline=slice_deadline,
                    stable_ms=self._AWAIT_STABLE_MS,
                    poll_ms=self._AWAIT_POLL_MS)
                return self._turn_result("idle", "idle", sess,
                                         envelope_bytes=raw)
            except paths.EnvelopeNotWritten:
                pass                       # no fresh file yet; await paced the slice
            except paths.EnvelopeRejected as e:
                # await_envelope only reads AFTER the file is size+mtime-stable,
                # so a rejection here is a COMPLETE-but-invalid envelope (wrong
                # gen / turn_uuid / shape / group-writable mode) — deterministic;
                # re-polling never fixes it. Surface a distinct TERMINAL outcome
                # (fail fast, diagnosable, never a bad accept) instead of masking
                # it as idle_no_envelope and burning the whole budget while
                # busy-spinning capture_pane (review findings #1/#6/#9).
                return self._turn_result("envelope_rejected", "idle", sess,
                                         envelope_bytes=None, detail=str(e))
            except paths.EnvelopeIncomplete:
                # Stable size but not-yet-parseable JSON: transient per the
                # read-back contract, so keep polling — but await_envelope
                # returns early here, so pace explicitly to avoid busy-spinning
                # the screen capture (review finding #6).
                time.sleep(self._AWAIT_POLL_MS / 1000.0)

            # 2) screen read — only for awaiting_input / dead.
            stripped = strip_screen(sess.tmux.capture_pane(target))
            footer = footer_of(stripped)
            screen_state, meta = classify(
                stripped, footer, env_present=os.path.exists(env_path),
                prev_timer=prev_timer, composer_seen=True)
            prev_timer = meta.get("timer", prev_timer)
            state = screen_state

            if on_event and screen_state in ("thinking", "streaming"):
                if on_event(screen_state, meta) is False:
                    return self._turn_result("client_gone", screen_state, sess,
                                             envelope_bytes=None)

            if screen_state == "awaiting_input":
                return self._turn_result(
                    "awaiting_input", screen_state, sess, envelope_bytes=None,
                    alive=True, screen=meta.get("screen"),
                    kind=meta.get("kind"))

            if screen_state == "dead":
                # classify 'dead' is only a CANDIDATE; corroborate with
                # has-session/#{pane_dead} and require it to persist (risk #13).
                try:
                    alive = sess.tmux.has_session("t")
                    pane_dead = sess.tmux.pane_dead(target)
                except Exception:
                    alive, pane_dead = False, True
                if (not alive) or pane_dead:
                    if dead_since is None:
                        dead_since = time.monotonic()
                    elif (time.monotonic() - dead_since) >= self._DEAD_QUIESCENT_S:
                        return self._turn_result("dead", "dead", sess,
                                                 envelope_bytes=None,
                                                 alive=False)
                else:
                    dead_since = None
            else:
                dead_since = None
            # the bounded await_envelope slice above already paces this loop.

        # Budget elapsed with no fresh file -> idle_no_envelope (never stale).
        return self._turn_result("idle_no_envelope", state, sess,
                                 envelope_bytes=None)

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
        created = s.tmux.get_option("t", "@wcb_created")
        nonce = s.tmux.get_option("t", "@wcb_nonce")
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

    # --- reaper (orphan eviction) ------------------------------------------
    def reap(self):
        """One reaper pass. Evict ONLY sessions that are (a) READY (not mid-
        reconstruct), (b) not busy, (c) past the create grace, and (d) confirmed
        gone on REAP_STRIKES consecutive passes. Any live confirmation resets the
        strike count. tmux I/O happens OUTSIDE the structural lock (design (1))."""
        with self._lock:
            candidates = list(self._sessions.items())
        to_evict = []
        for sid, sess in candidates:
            if sess.status != "READY" or not sess.ready.is_set():
                continue                                  # mid-reconstruct
            if sess.turn_lock.locked():
                continue                                  # busy
            if (time.time() - sess.created_at) < GRACE_SECONDS:
                continue                                  # within create grace
            try:
                alive = sess.tmux.has_session("t")
            except Exception:
                # An exception is NOT a confirmed-gone strike here; transient-
                # vs-authoritative classification is refined in the reaper-thread
                # task. Conservatively skip this pass.
                continue
            if alive:
                sess._gone_strikes = 0
                continue
            sess._gone_strikes = getattr(sess, "_gone_strikes", 0) + 1
            if sess._gone_strikes >= REAP_STRIKES:
                to_evict.append((sid, sess))
        for sid, sess in to_evict:
            self._evict(sid, sess)

    def _evict(self, sid, sess):
        """Confined teardown of a confirmed-gone session. Re-check under the
        structural lock that we are not racing a turn that just started — before
        we touch the server AND again before deleting."""
        with self._lock:
            if self._sessions.get(sid) is not sess or sess.turn_lock.locked():
                return
        try:
            sess.tmux.kill_server()
        except Exception:
            pass
        self._confined_rmtree(sess)
        with self._lock:
            if self._sessions.get(sid) is sess and not sess.turn_lock.locked():
                del self._sessions[sid]
        self._list_cache = None                           # invalidate /list cache

    def _confined_rmtree(self, sess):
        """realpath-confirm the rendezvous dir is under self._base, then rmtree.

        RECONCILED: paths.assert_confined RETURNS None and RAISES PathSafetyError
        (it is neither a PermissionError nor a path-returning call — the plan's
        `confined = assert_confined(...)` / `except PermissionError` was authoring
        drift). Shared confined-rm for the reaper; delete keeps its own
        raise-on-unconfined contract (a caller-facing security signal)."""
        rdir = getattr(sess, "rendezvous_dir", None)
        if not rdir:
            return
        try:
            paths.assert_confined(rdir, self._base)
        except paths.PathSafetyError:
            return                                        # refuse unconfined rm
        shutil.rmtree(rdir, ignore_errors=True)


REGISTRY = _Registry()
