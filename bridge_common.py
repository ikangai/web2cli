"""Shared logic between the macOS (rumps) and Windows (pystray) tray apps.

Anything that isn't OS-specific lives here so the two front-ends can't drift.
Platform-specific concerns (config path, save-with-permissions, clipboard)
stay in the per-OS modules.
"""
import json
import os

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
