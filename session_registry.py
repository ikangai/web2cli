"""In-memory reconstructable cache over `tmux list-sessions`.

The registry is NEVER the source of truth for liveness — `has_session` is.
Two-level locking: a module-level structural Lock guards dict mutation ONLY
(never wraps tmux I/O or a stream loop); a per-_Session turn Lock serializes
turns. See design (1) "Registry & locking".
"""
import threading
import time

from fsm import classify, footer_of, strip_screen


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
