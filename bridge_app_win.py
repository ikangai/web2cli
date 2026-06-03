#!/usr/bin/env python3
"""Windows tray app for the web2cli server. Mirrors bridge_app.py (macOS)."""
import os
import secrets
import subprocess
import threading
import tkinter as tk
from http.server import ThreadingHTTPServer
from pathlib import Path
from tkinter import messagebox, simpledialog

import pystray
from PIL import Image, ImageDraw

import bridge_common
from bridge_common import HOST, load_config, resolve_port, resolve_token, set_active_token
from server import Handler, __version__ as VERSION

CONFIG_PATH = Path(
    os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
) / "WebCLIBridge" / "config.json"


def save_config(cfg):
    bridge_common.write_config_text(CONFIG_PATH, cfg)
    # Mirror the macOS chmod 0o600 — restrict to the current user only so the
    # bearer token isn't readable by other local accounts that gain access to
    # %LOCALAPPDATA% (e.g. via roaming-profile / weakened inherited ACLs).
    user = os.environ.get("USERNAME")
    if not user:
        return
    try:
        subprocess.run(
            ["icacls", str(CONFIG_PATH),
             "/inheritance:r", "/grant:r", f"{user}:F"],
            check=False, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, OSError):
        pass


def make_icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon(
        [(36, 4), (10, 36), (26, 36), (20, 60), (54, 26), (36, 26), (44, 4)],
        fill=(255, 204, 0, 255),
        outline=(40, 40, 40, 255),
    )
    return img


def _with_root(fn):
    root = tk.Tk()
    root.withdraw()
    try:
        return fn(root)
    finally:
        root.destroy()


def _clip_copy(text):
    # PowerShell's Set-Clipboard handles Unicode end-to-end. clip.exe needs a
    # UTF-16LE BOM or input in the active OEM codepage; passing raw UTF-16LE
    # without a BOM (the previous impl) produced NUL-interleaved garbage on
    # paste even for ASCII tokens.
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$input | Set-Clipboard"],
            input=text,
            text=True,
            encoding="utf-8",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


class BridgeApp:
    def __init__(self):
        self.cfg = load_config(CONFIG_PATH)
        self.port = resolve_port(self.cfg)
        self.token = resolve_token(self.cfg)
        set_active_token(self.token)
        self.httpd = None
        self.thread = None
        self.icon = pystray.Icon(
            "WebCLIBridge",
            icon=make_icon_image(),
            title=self._tooltip(),
            menu=self._build_menu(),
        )

    def _is_running(self):
        return self.httpd is not None

    def _state_summary(self):
        return bridge_common.state_summary(self._is_running(), self.port, self.token)

    def _tooltip(self):
        return f"WebCLIBridge — {self._state_summary()}"

    def _status_text(self):
        return f"Status: {self._state_summary()}"

    def _refresh(self):
        self.icon.title = self._tooltip()
        self.icon.update_menu()

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda _: self._status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start", self.start, enabled=lambda _: not self._is_running()),
            pystray.MenuItem("Stop", self.stop, enabled=lambda _: self._is_running()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Change Port…", self.change_port),
            pystray.MenuItem(
                "Token",
                pystray.Menu(
                    pystray.MenuItem("Generate", self.generate_token),
                    pystray.MenuItem("Copy", self.copy_token, enabled=lambda _: bool(self.token)),
                    pystray.MenuItem("Clear", self.clear_token, enabled=lambda _: bool(self.token)),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("About", self.show_about),
            pystray.MenuItem("Quit", self.quit),
        )

    def start(self, _icon=None, _item=None):
        if self._is_running():
            self.icon.notify(f"Already running on :{self.port}")
            return
        try:
            self.httpd = ThreadingHTTPServer((HOST, self.port), Handler)
        except OSError as e:
            _with_root(lambda root: messagebox.showerror(
                "WebCLIBridge",
                f"Could not bind {HOST}:{self.port}\n\n{e}",
                parent=root,
            ))
            self.httpd = None
            return
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self._refresh()

    def stop(self, _icon=None, _item=None):
        if not self._is_running():
            self.icon.notify("Already stopped")
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        self.thread = None
        self._refresh()

    def _restart_if_running(self):
        if self._is_running():
            self.stop()
            self.start()

    def change_port(self, _icon=None, _item=None):
        new_port = _with_root(lambda root: simpledialog.askinteger(
            "Change Port",
            "Listen on which port?",
            initialvalue=self.port,
            minvalue=1,
            maxvalue=65535,
            parent=root,
        ))
        if new_port is None or new_port == self.port:
            return
        self.port = new_port
        self.cfg["port"] = new_port
        save_config(self.cfg)
        self._restart_if_running()
        self._refresh()

    def generate_token(self, _icon=None, _item=None):
        new_token = secrets.token_urlsafe(32)
        self.token = new_token
        self.cfg["token"] = new_token
        save_config(self.cfg)
        set_active_token(new_token)
        self._refresh()
        should_copy = _with_root(lambda root: messagebox.askyesno(
            "WebCLIBridge",
            f"New token generated. Copy to clipboard?\n\n{new_token}",
            parent=root,
        ))
        if should_copy:
            _clip_copy(new_token)

    def copy_token(self, _icon=None, _item=None):
        if not self.token:
            return
        if _clip_copy(self.token):
            self.icon.notify("Token copied")
        else:
            _with_root(lambda root: messagebox.showinfo(
                "WebCLIBridge",
                f"Could not copy. Token:\n\n{self.token}",
                parent=root,
            ))

    def clear_token(self, _icon=None, _item=None):
        if not self.token:
            return
        self.token = None
        self.cfg.pop("token", None)
        save_config(self.cfg)
        set_active_token(None)
        self._refresh()

    def show_about(self, _icon=None, _item=None):
        auth_line = "Token authentication: ON" if self.token else "Token authentication: OFF"
        body = (
            f"Version {VERSION}\n\n"
            f"Localhost HTTP-to-CLI bridge.\n"
            f"Address: {HOST}:{self.port}\n"
            f"{auth_line}\n\n"
            f"Config: {CONFIG_PATH}"
        )
        _with_root(lambda root: messagebox.showinfo("WebCLIBridge", body, parent=root))

    def quit(self, _icon=None, _item=None):
        self.stop()
        # Tear down any detached wcb_* claude sessions so a quit never leaves
        # orphaned tmux servers behind (design §4 tray-quit hygiene).
        try:
            import session_registry
            session_registry.kill_all_wcb()
        except Exception:
            pass
        self.icon.stop()

    def run(self):
        def setup(icon):
            icon.visible = True
            self.start()
        self.icon.run(setup=setup)


if __name__ == "__main__":
    BridgeApp().run()
