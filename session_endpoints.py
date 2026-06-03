"""HTTP layer for the `/session/*` route family (SessionMixin).

This is the cleanup-security HUB: it owns routing (`_dispatch_session`), the
mandatory-token + origin-allowlist gates, origin-reflecting CORS (NEVER `*`),
and the exception->HTTP error ladder. `server.Handler` mixes this in; the legacy
`/run` + `/stream` paths keep their own `_send`/`_cors` and are untouched.

The registry handle is the module-level `REGISTRY` (referenced by name so tests
monkeypatch `session_endpoints.REGISTRY` to inject a temp-base registry — the
same seam the create/list unit tests already use).

Security baseline (design §2, risk #1): every `/session/*` request must present a
matching bearer token AND an allowlisted Origin (or be a non-browser caller with
no Origin / a same-origin fetch); cap-bound routes additionally require the
per-session `cap`. A `*`-CORS + token-optional default would be a drive-by RCE,
so the token is MANDATORY here even though legacy `/run` leaves it optional.
"""
import base64
import hmac
import json
import os
import time

import paths
import session_registry as sr
from fsm import classify, footer_of, strip_screen
from paths import safe_open_nofollow
from session_registry import REGISTRY, DocNotStaged
from tmux_session import NAMED_KEYS, _TmuxError

# 16 MiB — the same cap the legacy /run path enforces.
MAX_BODY_BYTES = 16 * 1024 * 1024

# The default claude launch line (production never accepts a caller socket).
DEFAULT_CLAUDE_ARGV = ["claude", "--permission-mode", "bypassPermissions"]

# GET is allowed ONLY for the read-only listing.
_GET_ROUTES = frozenset({"/session/list"})


class _BodyTooLarge(Exception):
    """Request body exceeds MAX_BODY_BYTES -> 413."""


class _TmuxMissing(Exception):
    """tmux binary unavailable -> 503."""


class _TurnBusy(Exception):
    """A control op could not acquire the per-session turn lock -> 409."""


class SessionMixin:
    # ------------------------------------------------------------------ #
    # token / origin / CORS                                              #
    # ------------------------------------------------------------------ #
    def _session_token(self):
        # MANDATORY for /session/* (None => every call is 401). Distinct from
        # the legacy /run path, where a missing token means "auth disabled".
        return os.environ.get("WEB_CLI_BRIDGE_TOKEN") or None

    def _allowed_origins(self):
        # Union of both documented names (the plan drifted between
        # WCB_ALLOWED_ORIGINS and WCB_RWA_ORIGIN); read both, comma-split.
        raw = "%s,%s" % (os.environ.get("WCB_ALLOWED_ORIGINS", ""),
                         os.environ.get("WCB_RWA_ORIGIN", ""))
        return {o.strip() for o in raw.split(",") if o.strip()}

    def _origin_ok(self):
        """Origin allowlist gate (runs BEFORE any work; risk #1).

        - Origin present  -> must be in the allowlist.
        - Origin absent    -> a non-browser caller (curl) or a same-origin nav;
          allow only when Sec-Fetch-Site is same-origin/none/absent, so a
          cross-site browser request that omits Origin is still rejected.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return self.headers.get("Sec-Fetch-Site") in (None, "same-origin",
                                                           "none")
        return origin in self._allowed_origins()

    def _session_authorized(self):
        token = self._session_token()
        if not token:
            return False                      # MANDATORY: unset => reject all
        header = self.headers.get("Authorization") or ""
        scheme, _, payload = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        # compare_digest raises TypeError on non-ASCII str — degrade to a plain
        # auth failure (401), never let it bubble to a 500.
        try:
            return hmac.compare_digest(payload, token)
        except (TypeError, ValueError):
            return False

    def _session_cors(self):
        """CORS for /session/* — reflect ONLY an allowlisted origin, never `*`.

        Headers-only (no status line), so it composes with both _session_json
        and the raw SSE / get-envelope responses.
        """
        origin = self.headers.get("Origin")
        if origin and origin in self._allowed_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization")

    # ------------------------------------------------------------------ #
    # body + response helpers                                            #
    # ------------------------------------------------------------------ #
    def _read_session_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0:
            raise ValueError("Content-Length must be non-negative")
        if length > MAX_BODY_BYTES:
            raise _BodyTooLarge("request body exceeds %d bytes" % MAX_BODY_BYTES)
        if not length:
            return {}
        obj = json.loads(self.rfile.read(length))
        # A valid-but-non-object body (42, "x", [1,2], true) is a client error:
        # callers do body.get(...), which would AttributeError -> 500. Make it a
        # clean 400 via the dispatcher's ValueError arm.
        if not isinstance(obj, dict):
            raise ValueError("request body must be a JSON object")
        return obj

    def _session_json(self, status, obj, auth_challenge=False):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self._session_cors()
        if auth_challenge:
            self.send_header("WWW-Authenticate", 'Bearer realm="WebCLIBridge"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ #
    # dispatcher (routing + gates + ladder)                              #
    # ------------------------------------------------------------------ #
    def _dispatch_session(self, method):
        # 1) Origin gate FIRST — before token, before any tmux work (risk #1).
        if not self._origin_ok():
            return self._session_json(403, {"error": "forbidden origin"})
        # 2) Mandatory token.
        if not self._session_authorized():
            return self._session_json(401, {"error": "unauthorized"},
                                      auth_challenge=True)
        try:
            return self._route_session(method)
        except _BodyTooLarge as e:
            return self._session_json(413, {"error": str(e)})
        except paths.EnvelopeRejected:
            return self._session_json(422, {"error": "envelope_rejected"})
        except paths.EnvelopeNotWritten:
            return self._session_json(404, {"error": "envelope_not_written"})
        except paths.EnvelopeIncomplete:
            return self._session_json(404, {"error": "envelope_incomplete"})
        except DocNotStaged:
            return self._session_json(409, {"error": "doc_not_staged"})
        except (sr.SessionBusy, sr._SessionBusy, _TurnBusy):
            return self._session_json(409, {"error": "session busy"})
        except sr.MaxSessionsReached:
            return self._session_json(429, {"error": "max concurrent sessions"})
        except paths.PathSafetyError:
            return self._session_json(403, {"error": "forbidden path"})
        except PermissionError:
            return self._session_json(403, {"error": "forbidden"})
        except sr._NotFound:
            return self._session_json(404, {"error": "session not found"})
        except _TmuxMissing:
            return self._session_json(503, {"error": "tmux not available"})
        except _TmuxError as e:
            # 503 ONLY for a missing tmux binary (TmuxClient raises that as
            # "tmux binary not found: ..."). A session/target-gone _TmuxError
            # ("session not found") must stay 502 — the broad "not found" sniff
            # would mis-map it to 503.
            status = 503 if "binary not found" in str(e).lower() else 502
            return self._session_json(status, {"error": "tmux error"})
        except (NotADirectoryError, FileNotFoundError) as e:
            return self._session_json(400, {"error": "invalid cwd: %s" % e})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
            return self._session_json(400, {"error": str(e)})
        except Exception:
            # Never leak internal exception text to the client.
            return self._session_json(500, {"error": "internal server error"})

    def _route_session(self, method):
        path = self.path
        if method == "GET":
            if path in _GET_ROUTES:
                return self._do_list()
            return self._session_json(405, {"error": "method not allowed"})

        # POST routes.
        if path == "/session/create":
            return self._do_create()            # self-writing (mints the cap)
        if path == "/session/list":
            return self._do_list()              # self-writing
        body = self._read_session_body() or {}
        sid = body.get("session_id")
        cap = body.get("cap")
        if path == "/session/capture":
            return self._session_json(200, self._do_capture(sid, cap))
        if path == "/session/send-key":
            return self._session_json(
                200, self._do_send_key(sid, cap, body.get("keys")))
        if path == "/session/interrupt":
            return self._session_json(200, self._do_interrupt(sid, cap))
        if path == "/session/replay":
            return self._session_json(
                200, self._do_replay(sid, cap, body.get("from_offset", 0)))
        if path == "/session/delete":
            return self._session_json(200, self._do_delete(sid, cap))
        if path == "/session/get-envelope":
            # Writes its own (application/json, byte-exact) response.
            return self._do_get_envelope_response(sid, cap,
                                                  body.get("turn_uuid"))
        if path == "/session/stream":
            # Writes its own response (409 precheck, then an SSE 200 stream).
            return self._do_stream(body)
        return self._session_json(404, {"error": "not found"})

    # ------------------------------------------------------------------ #
    # per-session capability gate (cap-bound routes)                     #
    # ------------------------------------------------------------------ #
    def _authz_session(self, session_id, cap):
        """Resolve a session and enforce its per-session capability.

        Raises ValueError (bad id -> 400) or PermissionError (cap mismatch ->
        403). For an already-cached session the cap is checked with NO tmux work
        (and no reconstruct placeholder left behind on mismatch); only an
        uncached id falls through to get_or_reconstruct.
        """
        sid = paths.validate_session_id(session_id)

        def _check(sess):
            if not isinstance(cap, str) or not sess.cap \
                    or not hmac.compare_digest(sess.cap, cap):
                raise PermissionError("cap mismatch")
            return sess

        cached = REGISTRY.peek(sid)
        if cached is not None and cached.status == "READY":
            return _check(cached)
        return _check(REGISTRY.get_or_reconstruct(sid))

    # ------------------------------------------------------------------ #
    # create / list                                                      #
    # ------------------------------------------------------------------ #
    def _do_create(self):
        body = self._read_session_body() or {}
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            self._session_json(400, {"error": "cwd is required"})
            return
        cols = int(body.get("cols", 120))
        rows = int(body.get("rows", 40))
        # risk #6: NEVER forward a caller-supplied socket — the registry derives
        # wcb_<id>. socket_override stays a test-only registry seam.
        try:
            s = REGISTRY.create(cwd=cwd, cols=cols, rows=rows,
                                claude_argv=list(DEFAULT_CLAUDE_ARGV))
        except sr.MaxSessionsReached:
            self._session_json(429, {"error": "max concurrent sessions"})
            return
        except (ValueError, NotADirectoryError, FileNotFoundError) as e:
            self._session_json(400, {"error": "invalid cwd: %s" % e})
            return
        self._session_json(200, {
            "session_id": s.sid,
            "cap": s.cap,
            "rendezvous_dir": s.rendezvous_dir,
            "created_at": s.created_at,
        })

    def _do_list(self):
        self._session_json(200, {"sessions": REGISTRY.list_sessions()})

    # ------------------------------------------------------------------ #
    # cap-bound control routes (return a dict; the dispatcher wraps 200)  #
    # ------------------------------------------------------------------ #
    def _screen_state(self, sess):
        screen = strip_screen(sess.tmux.capture_pane(sess.pane))
        # Read composer_seen directly (always set in _Session.__init__, and
        # restored to True on reconstruct) — a True default here would mask a
        # genuinely-starting session as already-composed.
        state, _meta = classify(screen, footer_of(screen), env_present=False,
                                prev_timer=None, composer_seen=sess.composer_seen)
        return screen, state

    def _do_capture(self, session_id, cap):
        sess = self._authz_session(session_id, cap)
        screen, state = self._screen_state(sess)
        return {"screen": screen, "state": state,
                "log_offset": REGISTRY.current_offset(sess)}

    def _do_send_key(self, session_id, cap, keys):
        sess = self._authz_session(session_id, cap)
        if not isinstance(keys, list) or not keys:
            raise ValueError("keys must be a non-empty list")
        for k in keys:
            if not isinstance(k, str):
                raise TypeError("each key must be a string")
        # A standalone send-key holds the turn lock so it cannot interleave with
        # a streaming turn (design risk #10). A held lock => 409, never a queue.
        if not sess.turn_lock.acquire(timeout=2.0):
            raise _TurnBusy("session busy")
        try:
            for k in keys:
                if k in NAMED_KEYS:
                    sess.tmux.send_keys(sess.pane, k)
                else:
                    sess.tmux.send_text(sess.pane, k)
            _screen, state = self._screen_state(sess)
            return {"ok": True, "state": state}
        finally:
            sess.turn_lock.release()

    def _do_replay(self, session_id, cap, from_offset):
        sess = self._authz_session(session_id, cap)
        if isinstance(from_offset, bool) or not isinstance(from_offset, int) \
                or from_offset < 0:
            raise ValueError("from_offset must be a non-negative int")
        if not sess.turn_lock.acquire(timeout=2.0):
            raise _TurnBusy("session busy")
        try:
            # A CLOSED [from_offset, snapshot_end) slice taken under the lock —
            # never read-to-live-EOF (design §2). Offsets are global (rotation-
            # adjusted) via log_offset_base so they stay monotonic across a
            # rotation.
            base = getattr(sess, "log_offset_base", 0)
            local_from = max(0, from_offset - base)
            try:
                local_end = os.path.getsize(sess.log_path)
            except OSError:
                local_end = 0
            data = b""
            if sess.log_path and local_end > local_from:
                fd = safe_open_nofollow(sess.log_path, os.O_RDONLY)
                try:
                    os.lseek(fd, local_from, os.SEEK_SET)
                    data = os.read(fd, local_end - local_from)
                finally:
                    os.close(fd)
            return {
                "bytes": base64.b64encode(data).decode("ascii"),
                "from_offset": from_offset,
                "end_offset": from_offset + len(data),
            }
        finally:
            sess.turn_lock.release()

    # ------------------------------------------------------------------ #
    # SSE turn stream + byte-exact envelope retrieval                    #
    # ------------------------------------------------------------------ #
    def _do_stream(self, body):
        """One held-lock turn streamed as SSE (design §2).

        Self-writing: returns a 409 (cap-checked, doc-staged, busy) BEFORE any
        header, or commits to a 200 text/event-stream. The turn lock is acquired
        BEFORE send_response(200) so a concurrent stream observably 409s instead
        of corrupting the turn; released in finally without killing the session.
        """
        sess = self._authz_session(body.get("session_id"), body.get("cap"))
        doc = body.get("doc")
        if doc is None:
            # After a reconstruct the rwa must re-stage the current-turn doc.
            return self._session_json(409, {"error": "doc_not_staged"})
        if not isinstance(doc, str):
            raise ValueError("doc must be a string")
        instruction = body.get("instruction", "")
        timeout = body.get("timeout", 120.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
                or timeout <= 0:
            raise ValueError("timeout must be a positive number")
        turn_uuid = REGISTRY.mint_turn_uuid()

        # Bounded acquire BEFORE committing to the stream => observable 409.
        if not sess.turn_lock.acquire(timeout=2.0):
            return self._session_json(409, {"error": "session busy"})

        # Everything past the acquire lives in ONE try whose finally releases the
        # lock — so even if a header write or the turn raises, the session is
        # never wedged at 409 (design: lock released in finally, session never
        # killed). `committed` tracks whether the 200 status line was sent: once
        # it is, an exception must NOT propagate to the dispatcher ladder (which
        # would write a SECOND HTTP response into the live SSE stream) — it
        # degrades to a single `done` event with reason=error.
        committed = False
        st = {"last": None, "keep": time.monotonic(), "alive": True}

        def on_event(state, meta):
            now = time.monotonic()
            if state != st["last"]:
                st["last"] = state
                data = {"state": state}
                if meta.get("kind"):
                    data["kind"] = meta["kind"]
                ok = self._write_sse("state", data)
                st["keep"] = now
            elif now - st["keep"] > 10:
                st["keep"] = now
                ok = self._write_sse("keepalive", {"t": now})
            else:
                return st["alive"]
            if not ok:
                st["alive"] = False
                return False
            return st["alive"]

        try:
            self.send_response(200)
            committed = True
            self._session_cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            outcome = REGISTRY.run_turn_locked(
                sess, instruction=instruction, doc=doc, turn_uuid=turn_uuid,
                timeout=timeout, on_event=on_event)

            if not st["alive"]:
                outcome = dict(outcome)
                outcome["reason"] = "client_gone"
            if outcome.get("reason") == "awaiting_input":
                self._write_sse("state", {
                    "state": "awaiting_input",
                    "kind": outcome.get("kind"),
                    "screen": outcome.get("screen"),
                })
            self._write_sse("done", {
                "reason": outcome["reason"],
                "state": outcome.get("state"),
                "log_offset": REGISTRY.current_offset(sess),
                "alive": outcome.get("alive"),
                "turn_uuid": turn_uuid,
            })
        except Exception:
            if not committed:
                raise            # nothing sent yet -> dispatcher ladder maps it
            # 200 already on the wire: emit a terminal error event, swallow so the
            # ladder never writes a second response into the stream (best-effort:
            # if the client is gone the write fails silently).
            try:
                self._write_sse("done", {
                    "reason": "error", "state": None, "log_offset": 0,
                    "alive": False, "turn_uuid": turn_uuid,
                })
            except Exception:
                pass
        finally:
            sess.turn_lock.release()

    def _do_get_envelope_response(self, session_id, cap, turn_uuid):
        """Return claude's envelope bytes VERBATIM (no json.dumps, no second
        LLM): the rwa runs parseBridgeEnvelope on exactly what claude wrote.

        Self-writing on success; pre-write failures (bad uuid -> 400, cap -> 403,
        absent -> 404, symlink/gen/shape -> 422, mid-rename -> retry then 404)
        propagate to the dispatcher ladder.
        """
        sess = self._authz_session(session_id, cap)
        paths.validate_turn_uuid(turn_uuid)
        env = paths.env_path(sess.rendezvous_dir, turn_uuid)
        paths.assert_confined(env, REGISTRY._base)
        try:
            raw = paths.read_envelope_bytes(env, turn_uuid)
        except paths.EnvelopeIncomplete:
            # Writer may be mid-rename — one brief retry, then let it raise (404).
            time.sleep(0.15)
            raw = paths.read_envelope_bytes(env, turn_uuid)
        self.send_response(200)
        self._session_cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _do_interrupt(self, session_id, cap):
        sess = self._authz_session(session_id, cap)
        # Guarded killpg: TmuxClient.interrupt only signals when the pane's
        # foreground pgid is valid AND != the session's own shell pid (never
        # tears the session down); falls back to an in-band C-c.
        sess.tmux.interrupt(sess.pane, getattr(sess, "shell_pid", 0) or 0)
        return {"ok": True}

    def _do_delete(self, session_id, cap):
        sess = self._authz_session(session_id, cap)
        REGISTRY.delete(sess.sid, cap)
        return {"ok": True}
