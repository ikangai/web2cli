#!/usr/bin/env python3
import hmac
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_POSIX = os.name == "posix"

__version__ = "0.3.1"


MAX_BODY_BYTES = 16 * 1024 * 1024  # 16 MiB — enough for reasonable stdin payloads
_STREAM_POLL = 0.5  # seconds; bounds how quickly /stream notices client/timeout state
_REAP_TIMEOUT = 1.0  # seconds; bounds how long we wait for a terminated child to exit
_PIPE_CLOSED = (BrokenPipeError, ConnectionResetError, OSError)


class _BodyTooLarge(Exception):
    pass


def _token():
    return os.environ.get("WEB_CLI_BRIDGE_TOKEN") or None


def _decode(data):
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _reap(proc, timeout=_REAP_TIMEOUT):
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _kill_tree(proc):
    """Terminate proc and any children it spawned.

    proc.kill() only signals the immediate PID — for a `shell=True` command
    that backgrounds work (e.g. `while true; do echo x; sleep 0.5; done`) the
    children would otherwise outlive the request. On POSIX we kill the whole
    process group; on Windows we shell out to taskkill /T.

    Race note: poll() and getpgid() are not atomic, but Popen owns the only
    handle so PID reuse can't happen until we wait(). If the child exits in
    between, killpg raises ProcessLookupError and we swallow it.
    """
    if proc.poll() is not None:
        return
    if _POSIX:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    else:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except (FileNotFoundError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


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
        # RFC 7235 §2.1: auth-scheme is case-insensitive.
        scheme, _, payload = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        # compare_digest raises TypeError on non-ASCII str inputs. Treat any
        # malformed bearer payload as a plain auth failure (401) instead of
        # letting the TypeError bubble to do_POST and surface as a 400.
        try:
            return hmac.compare_digest(payload, token)
        except (TypeError, ValueError):
            return False

    def _require_str(self, body, key, optional=True):
        # Distinguishes missing key (KeyError) from key present with non-string
        # value (TypeError). For optional fields, both missing and explicit
        # null are treated as "absent".
        if key not in body:
            if optional:
                return None
            raise KeyError(key)
        value = body[key]
        if value is None and optional:
            return None
        if not isinstance(value, str):
            raise TypeError(f"{key} must be a string")
        return value

    def _parse_body(self):
        # Transfer-Encoding: chunked would make Content-Length absent and the
        # body silently read as empty. Reject explicitly rather than yielding
        # a misleading "command missing" 400.
        te = (self.headers.get("Transfer-Encoding") or "").strip().lower()
        if te and te != "identity":
            raise ValueError("Transfer-Encoding not supported")
        # Negative Content-Length would make rfile.read(length) read until EOF
        # — an unbounded read disguised as a tiny declared length.
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0:
            raise ValueError("Content-Length must be non-negative")
        if length > MAX_BODY_BYTES:
            raise _BodyTooLarge(f"request body exceeds {MAX_BODY_BYTES} bytes")
        body = json.loads(self.rfile.read(length)) if length else {}
        command = self._require_str(body, "command", optional=False)
        cwd = self._require_str(body, "cwd")
        stdin_data = self._require_str(body, "stdin")
        timeout = body.get("timeout")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("timeout must be a number")
            if timeout <= 0:
                raise ValueError("timeout must be positive")
        return command, cwd, timeout, stdin_data

    def do_POST(self):
        try:
            if self.path not in ("/run", "/stream"):
                return self._send(405, {"error": "not found"})
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"}, auth_challenge=True)
            command, cwd, timeout, stdin_data = self._parse_body()
            if self.path == "/run":
                self._handle_run(command, cwd, timeout, stdin_data)
            else:
                self._handle_stream(command, cwd, timeout, stdin_data)
        except _BodyTooLarge as e:
            self._send(413, {"error": str(e)})
        except (KeyError, ValueError, TypeError, json.JSONDecodeError,
                FileNotFoundError, NotADirectoryError) as e:
            self._send(400, {"error": str(e)})
        except Exception:
            # Don't leak internal exception text (paths, environment, locals)
            # to the client. The full traceback still goes to stderr via the
            # default BaseHTTPRequestHandler error logging.
            self._send(500, {"error": "internal server error"})

    def _handle_run(self, command, cwd, timeout, stdin_data):
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            start_new_session=_POSIX,
        )
        stdin_bytes = stdin_data.encode("utf-8") if stdin_data is not None else None
        try:
            try:
                stdout, stderr = proc.communicate(input=stdin_bytes, timeout=timeout)
                self._send(200, {
                    "stdout": _decode(stdout),
                    "stderr": _decode(stderr),
                    "exit_code": proc.returncode,
                    "timed_out": False,
                })
            except subprocess.TimeoutExpired as e:
                # Mirror subprocess.run's behavior: e.stdout/e.stderr already
                # hold everything that was read before the timeout fired. On
                # Windows the communicate reader thread populates them only
                # after the second communicate post-kill.
                _kill_tree(proc)
                if not _POSIX:
                    try:
                        e.stdout, e.stderr = proc.communicate(timeout=_REAP_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        pass
                self._send(200, {
                    "stdout": _decode(e.stdout),
                    "stderr": _decode(e.stderr),
                    "exit_code": None,
                    "timed_out": True,
                })
        finally:
            # Belt-and-braces: any other communicate failure (OSError on a
            # broken stdin pipe, etc.) must not leave a child running.
            if proc.poll() is None:
                _kill_tree(proc)
                _reap(proc)

    def _handle_stream(self, command, cwd, timeout, stdin_data):
        # Start the process first — bad cwd raises before any header is sent,
        # so it converts cleanly to a 400 via the do_POST handler.
        # start_new_session puts the child in its own process group on POSIX
        # so _kill_tree can reap descendants in one syscall.
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            bufsize=0,
            start_new_session=_POSIX,
        )

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._cors()
            self.end_headers()

            self._start_stdin_pump(proc, stdin_data)
            chunks = self._start_output_readers(proc)
            timed_out, client_gone = self._pump_output(proc, chunks, timeout)

            if client_gone:
                _reap(proc)
                return
            if timed_out:
                self._drain_remaining(chunks)
                _reap(proc)
                exit_code = None
            else:
                exit_code = _reap(proc)
                if exit_code is None:
                    # Process closed both pipes but is still running
                    # (e.g. `exec >/dev/null; sleep 600`). Kill and report.
                    _kill_tree(proc)
                    _reap(proc)
                    timed_out = True
            self._sse_exit(exit_code, timed_out)
        except Exception as e:
            _kill_tree(proc)
            self._sse_exit(None, False, error=str(e))

    def _start_stdin_pump(self, proc, stdin_data):
        if stdin_data is None or proc.stdin is None:
            return
        # Write stdin on a dedicated thread. A synchronous write here would
        # deadlock if stdin exceeds the pipe buffer (~64 KiB) and the child
        # produces stdout/stderr before draining stdin — both ends blocked
        # on write, no progress.
        stdin_bytes = stdin_data.encode("utf-8")
        stdin_pipe = proc.stdin

        def _pump():
            try:
                stdin_pipe.write(stdin_bytes)
            except _PIPE_CLOSED:
                pass
            finally:
                try:
                    stdin_pipe.close()
                except _PIPE_CLOSED:
                    pass

        threading.Thread(target=_pump, daemon=True).start()

    def _start_output_readers(self, proc):
        # Reader threads + queue is the cross-platform approach.
        # select.select() doesn't work on pipe FDs on Windows.
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
        return chunks

    def _pump_output(self, proc, chunks, timeout):
        start = time.monotonic()
        open_streams = 2
        while open_streams > 0:
            if timeout is not None:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    _kill_tree(proc)
                    return True, False  # timed_out
                wait = min(remaining, _STREAM_POLL)
            else:
                wait = _STREAM_POLL
            try:
                label, payload = chunks.get(timeout=wait)
            except queue.Empty:
                continue
            if payload is None:
                open_streams -= 1
            elif not self._sse(label, payload.decode("utf-8", errors="replace")):
                # Client disconnected; don't let the subprocess keep running
                # on the server with no one consuming its output.
                _kill_tree(proc)
                return False, True  # client_gone
        return False, False

    def _drain_remaining(self, chunks, budget=_REAP_TIMEOUT):
        # After a kill, reader threads may still be queuing buffered output.
        # Forward what arrives within budget; do not block indefinitely.
        deadline = time.monotonic() + budget
        open_streams = 2
        while open_streams > 0 and time.monotonic() < deadline:
            try:
                label, payload = chunks.get(timeout=0.05)
            except queue.Empty:
                continue
            if payload is None:
                open_streams -= 1
            else:
                self._sse(label, payload.decode("utf-8", errors="replace"))

    def _write_sse(self, event, data):
        payload = json.dumps(data).encode("utf-8")
        try:
            self.wfile.write(f"event: {event}\ndata: ".encode("ascii"))
            self.wfile.write(payload)
            self.wfile.write(b"\n\n")
            self.wfile.flush()
            return True
        except _PIPE_CLOSED:
            return False

    def _sse(self, event, chunk):
        return self._write_sse(event, {"chunk": chunk})

    def _sse_exit(self, exit_code, timed_out, error=None):
        data = {"exit_code": exit_code, "timed_out": timed_out}
        if error:
            data["error"] = error
        self._write_sse("exit", data)

    def _send(self, status, payload, auth_challenge=False):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._cors()
        if auth_challenge:
            self.send_header("WWW-Authenticate", 'Bearer realm="WebCLIBridge"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
