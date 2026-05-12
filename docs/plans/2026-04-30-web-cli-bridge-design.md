# Web CLI Bridge — Design

A tiny localhost HTTP server that executes shell commands on behalf of local
HTML apps and returns the result as JSON.

## Goals

- Let local HTML apps invoke CLI tools on the host machine.
- Be the smallest reasonable thing that works.
- One file. Zero dependencies. Pure Python stdlib.

## Non-goals

- Running on anything other than the user's own machine.
- Authentication, command allowlists, or sandboxing.
- Streaming output, stdin support, per-request timeouts, env injection,
  logging, daemonization. (All deferred — easy to add if needed.)

## Architecture

- Single file: `server.py`.
- `ThreadingHTTPServer` bound to `127.0.0.1` on a configurable port
  (default `8765`).
- One `BaseHTTPRequestHandler` subclass with two methods: `OPTIONS` and `POST`.
- Threading lets concurrent calls from the HTML app run in parallel.
- Run with `python3 server.py [port]`.

## API

### `POST /run`

Request body (JSON):
```json
{ "command": "git status", "cwd": "/optional/path" }
```

- `command` (string, required): shell command line. Run via
  `subprocess.run(command, shell=True, ...)`, so pipes, redirects, globs,
  `&&`, `||`, env-var expansion all work.
- `cwd` (string, optional): working directory for the command. Defaults to
  the server's own working directory if omitted.

Response body (JSON, HTTP 200 when the command ran):
```json
{ "stdout": "...", "stderr": "...", "exit_code": 0 }
```

A non-zero exit code is **not** an HTTP error. The HTTP layer reports bridge
health; the JSON reports the command's outcome.

### `OPTIONS /run`

CORS preflight. Returns 204 with:
- `Access-Control-Allow-Origin: *`
- `Access-Control-Allow-Methods: POST, OPTIONS`
- `Access-Control-Allow-Headers: Content-Type`

### Error responses

- `400` — JSON parse failure, missing `command`, or `cwd` does not exist.
- `405` — `POST` to a path other than `/run`.
- `501` — methods without a handler (`GET`, `PUT`, `DELETE`, …). This is
  Python's stdlib default; we don't add `do_GET` etc. since rejecting them
  is exactly the goal.
- `500` — unexpected server error (caught at the top level so a bad command
  never crashes the server).

`GET` is deliberately not handled: this prevents drive-by `<img src>` /
`<script src>` requests from triggering commands.

## Implementation outline

```python
#!/usr/bin/env python3
import json, subprocess, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_POST(self):
        if self.path != "/run":
            return self._send(405, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            command = body["command"]
            cwd = body.get("cwd")
            result = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, errors="replace", cwd=cwd,
            )
            self._send(200, {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            })
        except (KeyError, json.JSONDecodeError,
                FileNotFoundError, NotADirectoryError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})

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
```

Key choices:

- `text=True, errors="replace"` — non-UTF-8 output becomes `?` rather than
  raising.
- Bad `cwd` raises `FileNotFoundError` / `NotADirectoryError`; caught as 400.
- Top-level `except Exception` keeps the server alive across any failure.

## Usage from HTML

```js
const res = await fetch("http://127.0.0.1:8765/run", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ command: "git -C ~/repo status" }),
});
const { stdout, stderr, exit_code } = await res.json();
```

## Security model

The server trusts every caller on `127.0.0.1` and runs every received
command as the user who launched the server. This is acceptable because:

- It only listens on the loopback interface.
- It never accepts `GET`, blocking the easy drive-by browser attacks
  (`<img src="http://127.0.0.1:.../run">`).

It is **not** acceptable in environments where:

- Untrusted JavaScript may run in any browser tab while the server is up
  (any tab can issue `fetch(..., {method: "POST"})` and succeed).
- Multiple users share the machine.

Mitigation if those constraints change: add a shared-secret token (~5 lines)
and stop the server when not in use.

## Known limitations (deferred)

1. **No timeout.** A long-running command holds a server thread indefinitely.
   Threading prevents it from blocking other requests; it does not free the
   thread. Add `timeout=` to `subprocess.run` if this becomes a problem.
2. **Buffered output.** Full stdout/stderr is held in memory before the
   response is sent. Fine for normal CLI output, bad for huge dumps.
3. **No stdin.** Commands that prompt interactively will hang.

## Files produced

- `server.py` — the entire bridge.

That's it. No `README.md`, no `requirements.txt`, no tests, no config files.
