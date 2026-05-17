#!/usr/bin/env python3
import hmac
import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.3.0"


def _token():
    return os.environ.get("WEB_CLI_BRIDGE_TOKEN") or None


def _decode(data):
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization"
        )

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _authorized(self):
        token = _token()
        if not token:
            return True
        header = self.headers.get("Authorization") or ""
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):], token)

    def _parse_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else {}
        command = body["command"]
        if not isinstance(command, str):
            raise TypeError("command must be a string")
        cwd = body.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise TypeError("cwd must be a string")
        timeout = body.get("timeout")
        if timeout is not None:
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
                raise TypeError("timeout must be a number")
            if timeout <= 0:
                raise ValueError("timeout must be positive")
        stdin_data = body.get("stdin")
        if stdin_data is not None and not isinstance(stdin_data, str):
            raise TypeError("stdin must be a string")
        return command, cwd, timeout, stdin_data

    def do_POST(self):
        try:
            if self.path not in ("/run", "/stream"):
                return self._send(405, {"error": "not found"})
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            command, cwd, timeout, stdin_data = self._parse_body()
            if self.path == "/run":
                self._handle_run(command, cwd, timeout, stdin_data)
            else:
                self._handle_stream(command, cwd, timeout, stdin_data)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError,
                FileNotFoundError, NotADirectoryError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _handle_run(self, command, cwd, timeout, stdin_data):
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                errors="replace",
                cwd=cwd,
                input=stdin_data,
                timeout=timeout,
            )
            self._send(200, {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "timed_out": False,
            })
        except subprocess.TimeoutExpired as e:
            self._send(200, {
                "stdout": _decode(e.stdout),
                "stderr": _decode(e.stderr),
                "exit_code": None,
                "timed_out": True,
            })

    def _handle_stream(self, command, cwd, timeout, stdin_data):
        # Start the process first — bad cwd raises before any header is sent,
        # so it converts cleanly to a 400 via the do_POST handler.
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            bufsize=0,
        )

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._cors()
            self.end_headers()

            if stdin_data is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(stdin_data.encode("utf-8"))
                except (BrokenPipeError, OSError):
                    pass
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

            # Pump stdout/stderr via reader threads + queue so the impl is
            # cross-platform — select.select() doesn't work on pipe FDs on
            # Windows, which previously broke /stream in the Windows tray build.
            chunks: "queue.Queue[tuple[str, bytes | None]]" = queue.Queue()

            def _reader(label, fileobj):
                try:
                    while True:
                        chunk = fileobj.read(4096)
                        if not chunk:
                            break
                        chunks.put((label, chunk))
                except (OSError, ValueError):
                    pass
                finally:
                    chunks.put((label, None))

            for label, fileobj in (("stdout", proc.stdout), ("stderr", proc.stderr)):
                threading.Thread(
                    target=_reader, args=(label, fileobj), daemon=True
                ).start()

            start = time.monotonic()
            timed_out = False
            client_gone = False
            open_streams = 2

            while open_streams > 0:
                if timeout is not None:
                    remaining = timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        proc.kill()
                        timed_out = True
                        break
                    wait = min(remaining, 0.5)
                else:
                    wait = 0.5
                try:
                    label, payload = chunks.get(timeout=wait)
                except queue.Empty:
                    continue
                if payload is None:
                    open_streams -= 1
                elif not self._sse(label, payload.decode("utf-8", errors="replace")):
                    # Client disconnected — don't let the subprocess keep
                    # running on the server with no one consuming its output.
                    proc.kill()
                    client_gone = True
                    break

            if timed_out:
                # Drain any chunks the reader threads queued before/after the
                # kill, with a short budget so we don't hang the request.
                drain_deadline = time.monotonic() + 1.0
                while open_streams > 0 and time.monotonic() < drain_deadline:
                    try:
                        label, payload = chunks.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    if payload is None:
                        open_streams -= 1
                    else:
                        self._sse(label, payload.decode("utf-8", errors="replace"))
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass

            if client_gone:
                # Reap the killed child; nothing to send (no client).
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                return

            # Bound the terminal wait — a process that closes stdout/stderr
            # but keeps running (e.g. `exec >/dev/null; sleep 600`) would
            # otherwise hang the handler indefinitely.
            if timed_out:
                exit_code = None
            else:
                try:
                    exit_code = proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                    exit_code = None
                    timed_out = True
            self._sse_exit(exit_code, timed_out)
        except Exception as e:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                self._sse_exit(None, False, error=str(e))
            except Exception:
                pass

    def _sse(self, event, chunk):
        payload = json.dumps({"chunk": chunk}).encode("utf-8")
        try:
            self.wfile.write(f"event: {event}\n".encode("ascii"))
            self.wfile.write(b"data: ")
            self.wfile.write(payload)
            self.wfile.write(b"\n\n")
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _sse_exit(self, exit_code, timed_out, error=None):
        data = {"exit_code": exit_code, "timed_out": timed_out}
        if error:
            data["error"] = error
        payload = json.dumps(data).encode("utf-8")
        try:
            self.wfile.write(b"event: exit\ndata: ")
            self.wfile.write(payload)
            self.wfile.write(b"\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
