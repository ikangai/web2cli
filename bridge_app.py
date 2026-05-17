#!/usr/bin/env python3
import json
import os
import secrets
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import rumps

from server import Handler

VERSION = "0.3.0"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "WebCLIBridge"
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def resolve_port(cfg):
    env = os.environ.get("WEB_CLI_BRIDGE_PORT")
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
    return os.environ.get("WEB_CLI_BRIDGE_TOKEN") or cfg.get("token") or None


def set_active_token(token):
    if token:
        os.environ["WEB_CLI_BRIDGE_TOKEN"] = token
    else:
        os.environ.pop("WEB_CLI_BRIDGE_TOKEN", None)


class BridgeApp(rumps.App):
    def __init__(self):
        super().__init__("⚡", quit_button=None)
        self.cfg = load_config()
        self.port = resolve_port(self.cfg)
        self.token = resolve_token(self.cfg)
        set_active_token(self.token)
        self.httpd = None
        self.thread = None
        self._build_menu()

    def _build_menu(self):
        self.status = rumps.MenuItem(self._status_text(running=False))
        self.start_item = rumps.MenuItem("Start", callback=self.start)
        self.stop_item = rumps.MenuItem("Stop", callback=None)
        self.change_port_item = rumps.MenuItem("Change Port…", callback=self.change_port)

        self.token_generate = rumps.MenuItem("Generate", callback=self.generate_token)
        self.token_copy = rumps.MenuItem(
            "Copy", callback=self.copy_token if self.token else None
        )
        self.token_clear = rumps.MenuItem(
            "Clear", callback=self.clear_token if self.token else None
        )
        token_menu = rumps.MenuItem("Token")
        token_menu.add(self.token_generate)
        token_menu.add(self.token_copy)
        token_menu.add(self.token_clear)

        self.menu = [
            self.status,
            None,
            self.start_item,
            self.stop_item,
            None,
            self.change_port_item,
            token_menu,
            None,
            rumps.MenuItem("About", callback=self.show_about),
            rumps.MenuItem("Quit", callback=self.quit),
        ]

    def _status_text(self, running):
        port_suffix = f" :{self.port}" if running else ""
        token_suffix = " (auth)" if self.token else ""
        return f"Status: {'running' if running else 'stopped'}{port_suffix}{token_suffix}"

    def _set_running(self, running):
        self.status.title = self._status_text(running)
        self.start_item.set_callback(None if running else self.start)
        self.stop_item.set_callback(self.stop if running else None)

    def _refresh_status(self):
        self._set_running(self._is_running())

    def _is_running(self):
        return self.httpd is not None

    def start(self, _):
        if self._is_running():
            rumps.notification(
                "WebCLIBridge", "Already running", f"Server is up on :{self.port}"
            )
            return
        try:
            self.httpd = ThreadingHTTPServer((HOST, self.port), Handler)
        except OSError as e:
            rumps.alert("WebCLIBridge", f"Could not bind {HOST}:{self.port}\n\n{e}")
            self.httpd = None
            return
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self._set_running(True)

    def stop(self, _):
        if not self._is_running():
            rumps.notification("WebCLIBridge", "Already stopped", "")
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        self.thread = None
        self._set_running(False)

    def _restart_if_running(self):
        if self._is_running():
            self.stop(None)
            self.start(None)

    def change_port(self, _):
        win = rumps.Window(
            title="Change Port",
            message="Listen on which port?",
            default_text=str(self.port),
            dimensions=(120, 22),
        )
        win.add_button("Cancel")
        response = win.run()
        if response.clicked != 1:
            return
        text = response.text.strip()
        try:
            new_port = int(text)
        except ValueError:
            rumps.alert("WebCLIBridge", "Port must be a number.")
            return
        if not (1 <= new_port <= 65535):
            rumps.alert("WebCLIBridge", "Port must be between 1 and 65535.")
            return
        if new_port == self.port:
            return
        self.port = new_port
        self.cfg["port"] = new_port
        save_config(self.cfg)
        self._restart_if_running()
        self._refresh_status()

    def generate_token(self, _):
        new_token = secrets.token_urlsafe(32)
        self.token = new_token
        self.cfg["token"] = new_token
        save_config(self.cfg)
        set_active_token(new_token)
        self.token_copy.set_callback(self.copy_token)
        self.token_clear.set_callback(self.clear_token)
        self._refresh_status()
        choice = rumps.alert(
            "WebCLIBridge",
            f"New token generated. Copy to clipboard?\n\n{new_token}",
            ok="Copy",
            cancel="Close",
        )
        if choice == 1:
            self._pbcopy(new_token)

    def copy_token(self, _):
        if not self.token:
            return
        if self._pbcopy(self.token):
            rumps.notification("WebCLIBridge", "Token copied", "")
        else:
            rumps.alert("WebCLIBridge", f"Could not copy. Token:\n\n{self.token}")

    def clear_token(self, _):
        if not self.token:
            return
        self.token = None
        self.cfg.pop("token", None)
        save_config(self.cfg)
        set_active_token(None)
        self.token_copy.set_callback(None)
        self.token_clear.set_callback(None)
        self._refresh_status()

    def show_about(self, _):
        auth_line = (
            "Token authentication: ON" if self.token else "Token authentication: OFF"
        )
        rumps.alert(
            "WebCLIBridge",
            (
                f"Version {VERSION}\n\n"
                f"Localhost HTTP-to-CLI bridge.\n"
                f"Address: {HOST}:{self.port}\n"
                f"{auth_line}\n\n"
                f"Config: {CONFIG_PATH}"
            ),
        )

    def _pbcopy(self, text):
        try:
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError, OSError):
            return False

    def quit(self, _):
        self.stop(None)
        rumps.quit_application()


if __name__ == "__main__":
    app = BridgeApp()
    app.start(None)
    app.run()
