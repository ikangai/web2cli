"""Path-safety + identity helpers for web_cli_bridge persistent sessions.

Pure stdlib. Mints session ids / caps / nonces, strictly validates caller-
supplied ids before they touch a path or argv, and confines every session
path under a verified 0700 base dir. Security-critical (design §1 identity,
§3 base-dir hardening + confinement).

Build-order note: the implementation plan referenced this module as a hard
dependency of the registry but never authored a task for it; this fills that
gap with the path-safety + identity surface the registry needs. The rendezvous
payload helpers (env_path/doc_path/read_envelope_bytes/put_doc/...) are added
here by the rendezvous file-primitives tasks.
"""
import os
import re
import secrets
import stat
import sys
import uuid

# session_id = uuid4().hex — 32 lowercase hex. Path-bound, so strictly anchored.
SESSION_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")
# turn_uuid = canonical uuid4 string (8-4-4-4-12 lowercase hex).
TURN_UUID_RE = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)


class PathSafetyError(RuntimeError):
    """A base dir failed verification, or a path escaped its confinement."""


# --- identity minting -------------------------------------------------------

def mint_session_id():
    """32-hex session id (uuid4().hex). Never trusts a caller for this."""
    return uuid.uuid4().hex


def mint_cap():
    """64-hex per-session capability token (presented on every /session call)."""
    return secrets.token_hex(32)


def mint_nonce():
    """16-hex per-incarnation dir nonce (so a restarted session never reuses
    a stale rendezvous dir)."""
    return secrets.token_hex(8)


def validate_session_id(sid):
    """Return `sid` iff it is a well-formed session id, else raise ValueError.

    Strict: only 32 lowercase hex. This is the gate that stops a caller-
    supplied id from reaching a filesystem path or a tmux argv (design §1).
    """
    if not isinstance(sid, str) or not SESSION_ID_RE.match(sid):
        raise ValueError("invalid session_id: %r" % (sid,))
    return sid


def validate_turn_uuid(turn_uuid):
    """Return `turn_uuid` iff it is a canonical uuid4 string, else ValueError."""
    if not isinstance(turn_uuid, str) or not TURN_UUID_RE.match(turn_uuid):
        raise ValueError("invalid turn_uuid: %r" % (turn_uuid,))
    return turn_uuid


# --- base dir + confinement -------------------------------------------------

def base_dir():
    """Rendezvous base under $HOME, never /tmp. Created 0700 if missing."""
    if sys.platform == "darwin":
        root = os.path.join(
            os.path.expanduser("~"),
            "Library", "Application Support", "WebCLIBridge", "rendezvous",
        )
    else:
        root = os.path.join(os.path.expanduser("~"), ".web_cli_bridge", "rendezvous")
    os.makedirs(root, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)  # makedirs honours umask; chmod defeats it
    return root


def verify_base_dir(base):
    """Verify `base` is safe to use, else raise PathSafetyError.

    lstat (NOT stat, so a symlink-to-dir is rejected): must be a real
    directory, not a symlink, owned by the effective uid, mode exactly 0700.
    A missing base raises PathSafetyError (the caller/bootstrap creates it).
    """
    try:
        st = os.lstat(base)
    except FileNotFoundError as e:
        raise PathSafetyError("rendezvous base missing: %s" % base) from e
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise PathSafetyError("base dir is not a real directory: %s" % base)
    if st.st_uid != os.geteuid():
        raise PathSafetyError("base dir not owned by euid: %s" % base)
    if stat.S_IMODE(st.st_mode) != 0o700:
        raise PathSafetyError("base dir not 0700: %s" % base)


def session_dir(base, sid, nonce):
    """Per-incarnation rendezvous dir path: <base>/wcb_<sid>_<nonce>.

    Pure join (no realpath) so the literal path stays under `base`; callers
    pair this with assert_confined() before creating it.
    """
    return os.path.join(base, "wcb_%s_%s" % (sid, nonce))


def assert_confined(path, base):
    """Raise PathSafetyError unless realpath(path) is `base` itself or strictly
    under realpath(base). Resolves symlinks/.. so a traversal or symlink escape
    cannot land outside the base (design §3). Works on not-yet-created paths
    (realpath resolves the existing prefix)."""
    rb = os.path.realpath(base)
    rp = os.path.realpath(path)
    if rp != rb and not rp.startswith(rb + os.sep):
        raise PathSafetyError("path escapes base: %r not under %r" % (path, base))


# --- rendezvous payload: doc staging ----------------------------------------

def doc_path(session_dir_path, turn_uuid) -> str:
    return os.path.join(session_dir_path, f"doc.{turn_uuid}.html")


def put_doc(session_dir_path, base, turn_uuid, data: bytes) -> str:
    """Atomically stage doc.<turn_uuid>.html: write .part (O_NOFOLLOW, 0600)
    then os.replace to the final name.

    Asserts \\n-only (no \\r) so anchor bytes match canonLF(persistDoc).
    Realpath-confines both the .part and final path under base first.
    """
    validate_turn_uuid(turn_uuid)          # rejects ../ , %, NUL, etc.
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("doc payload must be bytes")
    if b"\r" in data:
        raise ValueError("doc payload must be \\n-only (contains \\r)")
    final = doc_path(session_dir_path, turn_uuid)
    part = final + ".part"
    assert_confined(final, base)
    assert_confined(part, base)
    # O_NOFOLLOW + O_TRUNC + the confined session dir (0700) defeat symlink swaps.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(part, flags, 0o600)
    try:
        os.write(fd, bytes(data))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(part, final)                # atomic within the same dir
    return final


# --- envelope exception classes (shared by gen-sentinel + read-back) ---------

class EnvelopeRejected(Exception):
    """symlink / owner / mode / nlink / uuid / gen mismatch / bad shape -> 422."""


# --- rendezvous payload: gen sentinel ---------------------------------------

GEN_SENTINEL_RE = re.compile(rb"<!-- rwa:gen [0-9a-f-]{36} -->")


def inject_gen_sentinel(doc: bytes, turn_uuid) -> bytes:
    """Embed exactly one `<!-- rwa:gen <uuid> -->` comment.

    Replaces a prior sentinel if present, else appends. Caller stages the
    result; the prompt requires copying the uuid back into envelope.gen.
    """
    validate_turn_uuid(turn_uuid)
    if b"\r" in doc:
        raise ValueError("doc must be \\n-only before sentinel injection")
    tag = f"<!-- rwa:gen {turn_uuid} -->".encode()
    if GEN_SENTINEL_RE.search(doc):
        return GEN_SENTINEL_RE.sub(tag, doc, count=1)
    body = doc.rstrip(b"\n")
    return body + b"\n" + tag + b"\n"


def verify_envelope_sentinel(obj: dict, turn_uuid) -> None:
    """Reject (422) any envelope whose `gen` != the staged turn_uuid.

    Called by read_envelope_bytes (Task 33) so the echo guard is wired into
    the read path, not only tested in isolation (closes risk #4).
    """
    if obj.get("gen") != turn_uuid:
        raise EnvelopeRejected(
            f"gen sentinel mismatch: got {obj.get('gen')!r} want {turn_uuid!r}"
        )
