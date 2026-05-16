# web2cli

A tiny localhost HTTP server that executes shell commands on behalf of local
HTML apps and returns the result as JSON or streams it as Server-Sent Events.
Ships with an optional macOS menu bar app wrapper.

Current release: **v0.2.0** — see [`CHANGELOG.md`](CHANGELOG.md) for what changed.

## Components

- `server.py` — the bridge. Pure Python stdlib, no dependencies.
- `bridge_app.py` — macOS menu bar app (uses [`rumps`](https://pypi.org/project/rumps/)) that starts/stops the server and manages port/token config.
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

### `POST /run` — buffered

Request:
```json
{
  "command": "git status",
  "cwd": "/optional/path",
  "timeout": 30,
  "stdin": "optional input"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `command` | string | yes | Run via `subprocess.run(shell=True)` |
| `cwd` | string | no | Working directory |
| `timeout` | number | no | Seconds. Omit for no timeout |
| `stdin` | string | no | Sent on the process's stdin |

Response (HTTP 200 when the command ran or timed out):
```json
{
  "stdout": "...",
  "stderr": "...",
  "exit_code": 0,
  "timed_out": false
}
```

`exit_code` is `null` when the command timed out. A non-zero exit code is **not** an HTTP error — the HTTP layer reports bridge health; the JSON reports the command's outcome.

### `POST /stream` — Server-Sent Events

Same request shape as `/run`. Response is `text/event-stream` with `Connection: close`:

```
event: stdout
data: {"chunk": "hello\n"}

event: stderr
data: {"chunk": "warning: ...\n"}

event: exit
data: {"exit_code": 0, "timed_out": false}
```

- One `stdout` / `stderr` event per chunk, as the process produces output.
- Exactly one `exit` event terminates the stream.
- Non-UTF-8 bytes are decoded with `errors="replace"`.
- Stdin is still batch-only (sent with the request body, before streaming starts).

### `OPTIONS /run`, `OPTIONS /stream`

CORS preflight. Returns 204 with `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Methods: POST, OPTIONS`, and `Access-Control-Allow-Headers: Content-Type, Authorization`.

### Authentication (optional)

Set `WEB_CLI_BRIDGE_TOKEN` in the environment before starting the server. When set, every `POST /run` and `POST /stream` must carry:

```
Authorization: Bearer <token>
```

`OPTIONS` is never gated (CORS preflights cannot send `Authorization`). If unset, the server accepts every loopback caller (the original behavior).

The menu bar app exposes **Token ▸ Generate / Copy / Clear** to manage the token without editing env variables.

### Errors

- `400` — JSON parse failure, missing `command`, bad `cwd`, or malformed `timeout` / `stdin` / `command` / `cwd` field type.
- `401` — token required but missing or wrong.
- `405` — `POST` to a path other than `/run` or `/stream`.
- `500` — unexpected server error.
- `501` — methods without a handler (`GET`, `PUT`, `DELETE`, …).

`GET` is deliberately not handled, blocking drive-by `<img src>` / `<script src>` attacks.

## Calling from HTML

Buffered:

```js
const res = await fetch("http://127.0.0.1:8765/run", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    // Only if WEB_CLI_BRIDGE_TOKEN is set on the server:
    "Authorization": "Bearer YOUR_TOKEN",
  },
  body: JSON.stringify({
    command: "git -C ~/repo status",
    timeout: 10,
  }),
});
const { stdout, stderr, exit_code, timed_out } = await res.json();
```

Streamed — use `fetch` + a `ReadableStream` reader (EventSource only supports GET):

```js
const res = await fetch("http://127.0.0.1:8765/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ command: "tail -f /tmp/log" }),
});
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  let i;
  while ((i = buffer.indexOf("\n\n")) !== -1) {
    const event = buffer.slice(0, i);
    buffer = buffer.slice(i + 2);
    // parse `event: <name>\ndata: <json>` here
  }
}
```

## Configuration

The server reads two environment variables at request time:

- `WEB_CLI_BRIDGE_TOKEN` — enables bearer auth when set.

The menu bar app additionally reads two values from `~/Library/Application Support/WebCLIBridge/config.json` (created on first change):

```json
{
  "port": 8765,
  "token": "..."
}
```

Resolution order:

1. `WEB_CLI_BRIDGE_PORT` / `WEB_CLI_BRIDGE_TOKEN` env (wins if set).
2. `port` / `token` from the config file.
3. Defaults (`8765`, no token).

The config file is created with mode `0600`.

## Build a macOS `.app`

```bash
python3 -m pip install py2app rumps
python3 setup.py py2app
# → dist/WebCLIBridge.app
```

## Security model

The server listens only on `127.0.0.1` and trusts every caller on the loopback interface (unless `WEB_CLI_BRIDGE_TOKEN` is set). It runs every received command as the user who launched the server.

Acceptable for single-user development machines. **Not** acceptable for shared hosts or environments where untrusted JavaScript may issue `fetch` calls from any browser tab — enable the token there.

See [`docs/plans/2026-05-16-web-cli-bridge-v2-design.md`](docs/plans/2026-05-16-web-cli-bridge-v2-design.md) for the full design and [`CHANGELOG.md`](CHANGELOG.md) for release history.

## Known limitations

1. **Stdin is batch-only.** Commands that prompt interactively must have their full input supplied in the request body up front.
2. **No allowlist / sandboxing.** Any caller (with the token if set) can run any shell command the launching user can.
3. **Shell-level buffering.** Programs that line-buffer only when attached to a TTY may still appear chunky on `/stream`; wrap them with `stdbuf -oL …` or `unbuffer` when that matters.
