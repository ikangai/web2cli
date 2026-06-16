"""Shared logic between the macOS (rumps) and Windows (pystray) tray apps.

Anything that isn't OS-specific lives here so the two front-ends can't drift.
Platform-specific concerns (config path, save-with-permissions, clipboard)
stay in the per-OS modules.
"""
import json
import os
import subprocess

DEFAULT_PORT = 8765
HOST = "127.0.0.1"
TOKEN_ENV = "WEB_CLI_BRIDGE_TOKEN"
PORT_ENV = "WEB_CLI_BRIDGE_PORT"


def load_config(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def write_config_text(path, cfg, *, mode=None):
    # Atomic write: a crash mid-write would otherwise leave a truncated file,
    # which load_config silently treats as empty — losing the user's token.
    # Chmod is applied to the tmp file *before* the rename so the final path
    # never appears with world-readable umask permissions.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    if mode is not None:
        try:
            tmp.chmod(mode)
        except OSError:
            pass
    os.replace(tmp, path)


def resolve_port(cfg):
    env = os.environ.get(PORT_ENV)
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    try:
        return int(cfg.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def resolve_token(cfg):
    return os.environ.get(TOKEN_ENV) or cfg.get("token") or None


def set_active_token(token):
    if token:
        os.environ[TOKEN_ENV] = token
    else:
        os.environ.pop(TOKEN_ENV, None)


def state_summary(running, port, token):
    port_suffix = f" :{port}" if running else ""
    token_suffix = " (auth)" if token else ""
    return f"{'running' if running else 'stopped'}{port_suffix}{token_suffix}"


# --- PATH augmentation for GUI-launched bridges -----------------------------
#
# A macOS .app launched from Finder / Dock / a login item inherits only the
# bare `/usr/bin:/bin:/usr/sbin:/sbin` — it never sources ~/.zprofile/.zshrc,
# so user-installed CLIs are invisible. Both `claude` (native installer →
# ~/.local/bin) and `tmux` (/opt/homebrew/bin) then resolve to "command not
# found" (exit 127) when the bridge spawns a `shell=True` /run command or the
# /session/* tmux backend. Every such spawn inherits os.environ, so repairing
# PATH once at process startup fixes them all. (Run from a terminal the bridge
# already inherits the shell's full PATH and never hits this.)

# User-level bin dirs a login shell typically adds. Used as a fallback when the
# login-shell harvest fails, and always merged so ~/.local/bin is guaranteed.
_FALLBACK_BIN_DIRS = (
    "~/.local/bin",        # native `claude` installer puts the launcher here
    "~/.bun/bin",
    "~/bin",
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
)

_PATH_MARKER = "__WCB_PATH__"


def _harvest_login_path(timeout=3.0):
    """Return the PATH a login+interactive instance of the user's shell sets,
    or None if it can't be determined.

    This is the same trick GUI editors use to find user-installed tools. The
    markers fence the value off from any banner the rc files print to stdout,
    and the short timeout + broad except keep a slow/misconfigured shell from
    ever blocking startup (the caller still has _FALLBACK_BIN_DIRS)."""
    shell = os.environ.get("SHELL") or "/bin/sh"
    script = 'printf %%s "%s$PATH%s"' % (_PATH_MARKER, _PATH_MARKER)
    try:
        out = subprocess.run(
            [shell, "-ilc", script],
            capture_output=True, text=True, timeout=timeout,
        ).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    parts = out.split(_PATH_MARKER)
    if len(parts) < 3:
        return None
    return parts[1].strip() or None


def ensure_user_path():
    """Merge the user's real shell PATH into this process's PATH (idempotent).

    POSIX-only — Windows GUI apps inherit the full machine+user PATH from the
    registry and don't suffer this. Prepends any missing, real directories so
    user-installed tools take precedence, exactly as a terminal would resolve
    them. Returns the list of directories it added (for logging/tests)."""
    if os.name != "posix":
        return []
    existing = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    seen = set(existing)

    candidates = []
    harvested = _harvest_login_path()
    if harvested:
        candidates += harvested.split(os.pathsep)
    candidates += [os.path.expanduser(d) for d in _FALLBACK_BIN_DIRS]

    added = []
    for d in candidates:
        if d and d not in seen and os.path.isdir(d):
            added.append(d)
            seen.add(d)
    if added:
        os.environ["PATH"] = os.pathsep.join(added + existing)
    return added
