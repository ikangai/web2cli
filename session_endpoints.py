"""HTTP wrappers for /session/* create + list (SessionMixin).

Routing, CORS, and the auth ladder are owned by the cleanup-security
dispatcher (_dispatch_session); this module supplies only the _do_create /
_do_list handler bodies. The full STATES-aware capture/replay/stream handlers
live in their own components and share the canonical _do_* naming.
"""
from session_registry import REGISTRY, _MaxSessionsReached

# The default claude launch line (production never accepts a caller socket).
DEFAULT_CLAUDE_ARGV = ["claude", "--permission-mode", "bypassPermissions"]


class SessionMixin:
    def _do_create(self):
        body = self._read_session_body() or {}
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            self._session_json(400, {"error": "cwd is required"})
            return
        cols = int(body.get("cols", 120))
        rows = int(body.get("rows", 40))
        # CRITIQUE-FIX risk #6: NEVER forward a caller-supplied socket — the
        # registry derives wcb_<id>. socket_override is a test-only seam.
        try:
            s = REGISTRY.create(cwd=cwd, cols=cols, rows=rows,
                                claude_argv=list(DEFAULT_CLAUDE_ARGV))
        except _MaxSessionsReached:
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
