# web2cli

A tiny localhost HTTP server that executes shell commands on behalf of local
HTML apps and returns the result as JSON. Ships with an optional macOS menu
bar app wrapper.

## Components

- `server.py` — the bridge. Pure Python stdlib, no dependencies.
- `bridge_app.py` — macOS menu bar app (uses [`rumps`](https://pypi.org/project/rumps/)) that starts/stops the server.
- `setup.py` — `py2app` config to build a standalone `.app` bundle.

## Run

Bare server:

```bash
python3 server.py [port]   # default port: 8765
```

Menu bar app (macOS):

```bash
python3 -m pip install rumps
python3 bridge_app.py
```

## API

### `POST /run`

Request:
```json
{ "command": "git status", "cwd": "/optional/path" }
```

Response (HTTP 200 when the command ran):
```json
{ "stdout": "...", "stderr": "...", "exit_code": 0 }
```

A non-zero exit code is **not** an HTTP error. The HTTP layer reports bridge
health; the JSON reports the command's outcome.

CORS preflight (`OPTIONS /run`) returns 204 with `Access-Control-Allow-Origin: *`.

### Errors

- `400` — JSON parse failure, missing `command`, or `cwd` does not exist.
- `405` — `POST` to a path other than `/run`.
- `500` — unexpected server error.
- `501` — methods without a handler (`GET`, `PUT`, `DELETE`, …).

`GET` is deliberately not handled, blocking drive-by `<img src>` /
`<script src>` attacks.

## Calling from HTML

```js
const res = await fetch("http://127.0.0.1:8765/run", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ command: "git -C ~/repo status" }),
});
const { stdout, stderr, exit_code } = await res.json();
```

## Build a macOS `.app`

```bash
python3 -m pip install py2app rumps
python3 setup.py py2app
# → dist/WebCLIBridge.app
```

## Security model

The server listens only on `127.0.0.1` and trusts every caller on the
loopback interface. It runs every received command as the user who launched
the server. This is acceptable for single-user development machines but
**not** for shared hosts or environments where untrusted JavaScript may
issue `fetch` calls from any browser tab.

See [`docs/plans/2026-04-30-web-cli-bridge-design.md`](docs/plans/2026-04-30-web-cli-bridge-design.md) for the full design.

## Known limitations

1. No request timeout — a long-running command holds a thread indefinitely.
2. Buffered output — full stdout/stderr is held in memory before responding.
3. No stdin support — commands that prompt interactively will hang.
