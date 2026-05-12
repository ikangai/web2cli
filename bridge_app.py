#!/usr/bin/env python3
import threading
from http.server import ThreadingHTTPServer

import rumps

from server import Handler

HOST = "127.0.0.1"
PORT = 8765


class BridgeApp(rumps.App):
    def __init__(self):
        super().__init__("⚡", quit_button=None)
        self.status = rumps.MenuItem(self._status_text(running=False))
        self.start_item = rumps.MenuItem("Start", callback=self.start)
        self.stop_item = rumps.MenuItem("Stop", callback=None)
        self.menu = [
            self.status,
            None,
            self.start_item,
            self.stop_item,
            None,
            rumps.MenuItem("Quit", callback=self.quit),
        ]
        self.httpd = None
        self.thread = None

    def _status_text(self, running):
        return f"Status: running :{PORT}" if running else "Status: stopped"

    def _set_running(self, running):
        self.status.title = self._status_text(running)
        self.start_item.set_callback(None if running else self.start)
        self.stop_item.set_callback(self.stop if running else None)

    def start(self, _):
        if self.httpd:
            return
        try:
            self.httpd = ThreadingHTTPServer((HOST, PORT), Handler)
        except OSError as e:
            rumps.alert("web_cli_bridge", f"Could not bind {HOST}:{PORT}\n\n{e}")
            self.httpd = None
            return
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.thread.start()
        self._set_running(True)

    def stop(self, _):
        if not self.httpd:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        self.thread = None
        self._set_running(False)

    def quit(self, _):
        self.stop(None)
        rumps.quit_application()


if __name__ == "__main__":
    app = BridgeApp()
    app.start(None)
    app.run()
