#!/usr/bin/env python3
import hmac
import json
import os
import select
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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

            start = time.monotonic()
            timed_out = False
            labels = {
                proc.stdout.fileno(): "stdout",
                proc.stderr.fileno(): "stderr",
            }
            open_fds = set(labels)

            while open_fds:
                if timeout is not None:
                    remaining = timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        proc.kill()
                        timed_out = True
                        break
                    wait = min(remaining, 0.5)
                else:
                    wait = 0.5

                ready, _, _ = select.select(list(open_fds), [], [], wait)
                for fd in ready:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        open_fds.discard(fd)
                    else:
                        self._sse(labels[fd], chunk.decode("utf-8", errors="replace"))

            if timed_out:
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                for fd in list(open_fds):
                    try:
                        chunk = os.read(fd, 65536)
                        if chunk:
                            self._sse(labels[fd], chunk.decode("utf-8", errors="replace"))
                    except OSError:
                        pass

            exit_code = None if timed_out else proc.wait()
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
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

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
