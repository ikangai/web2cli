# web2cli

A tiny localhost HTTP server that executes shell commands on behalf of local
HTML apps and returns the result as JSON or streams it as Server-Sent Events.
Also hosts persistent interactive `claude` sessions in invisible tmux servers
(`/session/*`). Ships with optional macOS menu bar and Windows tray app wrappers.

Current release: **v0.4.0** — see [`CHANGELOG.md`](CHANGELOG.md) for what changed.

## Install

**macOS** (menu bar app):

```bash
curl -fsSL https://raw.githubusercontent.com/ikangai/web2cli/main/install.sh | sh
```

Drops `WebCLIBridge.app` into `/Applications` (asks for your password) and launches it.

**Windows** (system tray app):

```powershell
irm https://raw.githubusercontent.com/ikangai/web2cli/main/install.ps1 | iex
```

Installs `WebCLIBridge.exe` into `%LOCALAPPDATA%\Programs\WebCLIBridge\` (no admin needed), adds a Start Menu shortcut, and launches it. SmartScreen may show "Windows protected your PC" on first launch — click *More info → Run anyway*.

Both pull the latest pre-built bundle from [GitHub Releases](https://github.com/ikangai/web2cli/releases). Re-running either one-liner upgrades in place. For the bare server on other platforms see [Run](#run) below.

**Uninstall:**

```bash
# macOS
curl -fsSL https://raw.githubusercontent.com/ikangai/web2cli/main/install.sh | sh -s -- uninstall
```

```powershell
# Windows
$env:WEB2CLI_UNINSTALL='1'; irm https://raw.githubusercontent.com/ikangai/web2cli/main/install.ps1 | iex
```

Removes the bundle / executable and Start Menu shortcut. Config files at `~/Library/Application Support/WebCLIBridge/` (macOS) and `%LOCALAPPDATA%\WebCLIBridge\` (Windows) are kept.

## Components

- `server.py` — the bridge. Pure Python stdlib, no dependencies.
- `session_endpoints.py`, `session_registry.py`, `tmux_session.py`, `fsm.py`, `paths.py` — the `/session/*` persistent-claude-session backend (stdlib only; needs the `tmux` binary at runtime).
- `bridge_app.py` — macOS menu bar app (uses [`rumps`](https://pypi.org/project/rumps/)).
- `bridge_app_win.py` — Windows tray app (uses [`pystray`](https://pypi.org/project/pystray/) + [`Pillow`](https://pypi.org/project/Pillow/) + `tkinter`).
- `setup.py` — `py2app` config to build a standalone macOS `.app` bundle.
- `pyinstaller-win.spec` — PyInstaller config to build a standalone Windows `.exe` (run on GitHub Actions).

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

### `/session/*` — persistent claude sessions

Hosts a long-lived interactive `claude` TUI inside an invisible per-session tmux server and runs *edit turns* against it. The result of each turn travels through a **file rendezvous**, not the terminal screen: the bridge stages the document to a per-turn file, claude is prompted to write an `rwa-edit/1` edit-envelope JSON to a bridge-chosen path (scratch `.part` file, then an atomic rename as the completion signal), and the bridge returns those bytes verbatim. The tmux screen is only used for liveness and for surfacing interactive prompts. Requires `tmux` on the host (`503` otherwise).

Unlike `/run` and `/stream`, **every `/session/*` route requires the bearer token** (an unset `WEB_CLI_BRIDGE_TOKEN` rejects all session calls) and an allowlisted `Origin` (`WCB_ALLOWED_ORIGINS`, comma-separated; CORS reflects the allowlisted origin, never `*`). `create` returns a per-session capability secret `cap` that every other route must echo.

| Route | Body | Returns |
|---|---|---|
| `POST /session/create` | `{"cwd": "...", "cols"?, "rows"?}` | `{"session_id", "cap", "rendezvous_dir", "created_at"}` — launches `claude --permission-mode bypassPermissions` (argv is fixed server-side), auto-answers the workspace-trust prompt, `429` past 8 concurrent sessions |
| `POST /session/stream` | `{"session_id", "cap", "doc", "instruction", "timeout"?}` | SSE: `state` events (`thinking` / `streaming` / `awaiting_input`), `keepalive`, then one `done` with `reason` and the `turn_uuid`; `409` if a turn is already running |
| `POST /session/get-envelope` | `{"session_id", "cap", "turn_uuid"}` | claude's envelope bytes **verbatim**; `404` if not (yet) written, `422` if it fails the gen-sentinel / turn-uuid check |
| `POST /session/capture` | `{"session_id", "cap"}` | `{"screen", "state", "log_offset"}` — current screen snapshot |
| `POST /session/send-key` | `{"session_id", "cap", "keys": ["Down", "1", "Enter"]}` | sends keys (named keys or literal text) — e.g. to answer a menu surfaced as `awaiting_input` |
| `POST /session/interrupt` | `{"session_id", "cap"}` | guarded Ctrl-C of the running turn |
| `POST /session/replay` | `{"session_id", "cap", "from_offset"}` | base64 slice of the session log from a byte offset |
| `POST /session/delete` | `{"session_id", "cap"}` | tears the session down |
| `GET /session/list` | — | `{"sessions": [...]}` |

A typical edit turn: `create` once → per turn: `stream` (send doc + instruction, watch states) → on `done` with `reason: "idle"`, fetch the envelope with `get-envelope` → apply it client-side → next `stream`. Envelopes are per-turn and swept when the next turn starts, so fetch between turns. If a turn ends `awaiting_input`, inspect with `capture` and answer with `send-key`.

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
- `405` — `POST` to a path other than `/run`, `/stream`, or `/session/*`.
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

## Examples

Two self-contained demo pages in [`examples/`](examples/) (open directly from disk, no build step):

- [`claude-p-test.html`](examples/claude-p-test.html) — one-shot prompting over `POST /run`: the prompt travels base64-through-stdin into `claude -p`, so no shell quoting can break it. Fresh claude process per prompt, no conversation memory.
- [`claude-session-chat.html`](examples/claude-session-chat.html) — multi-turn chat over `/session/*`: one persistent claude instance answers every prompt (full conversation continuity), each turn running through `stream` → `get-envelope` so the answer arrives byte-exact via the file rendezvous. Includes a collapsible live view of the underlying TUI screen.

The session page needs the server started with a token and an allowlisted origin for `file://` pages, e.g. `WEB_CLI_BRIDGE_TOKEN=… WCB_ALLOWED_ORIGINS=null python3 server.py 8766`.

## Configuration

The server reads these environment variables at request time:

- `WEB_CLI_BRIDGE_TOKEN` — enables bearer auth on `/run` + `/stream` when set; **required** for any `/session/*` call.
- `WCB_ALLOWED_ORIGINS` (alias `WCB_RWA_ORIGIN`) — comma-separated Origin allowlist for `/session/*`; browser callers from other origins get `403`.
- `WCB_ALLOWED_CWD_ROOT` — when set, `/session/create` only accepts a `cwd` inside this root (default: any directory).

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

## Build a Windows `.exe`

Normally built by [`.github/workflows/release-windows.yml`](.github/workflows/release-windows.yml) on tag push. To build locally on Windows:

```powershell
python -m pip install pystray Pillow pyinstaller
pyinstaller --noconfirm pyinstaller-win.spec
# → dist\WebCLIBridge.exe
```

## Security model

The server listens only on `127.0.0.1` and trusts every caller on the loopback interface (unless `WEB_CLI_BRIDGE_TOKEN` is set). It runs every received command as the user who launched the server.

Acceptable for single-user development machines. **Not** acceptable for shared hosts or environments where untrusted JavaScript may issue `fetch` calls from any browser tab — enable the token there.

See [`docs/plans/2026-05-16-web-cli-bridge-v2-design.md`](docs/plans/2026-05-16-web-cli-bridge-v2-design.md) for the full design and [`CHANGELOG.md`](CHANGELOG.md) for release history.

## Known limitations

1. **Stdin is batch-only.** Commands that prompt interactively must have their full input supplied in the request body up front.
2. **No allowlist / sandboxing.** Any caller (with the token if set) can run any shell command the launching user can.
3. **Shell-level buffering.** Programs that line-buffer only when attached to a TTY may still appear chunky on `/stream`; wrap them with `stdbuf -oL …` or `unbuffer` when that matters.
4. **`/session/*` needs tmux.** The persistent-session routes return `503` where the `tmux` binary is unavailable — in practice they are macOS/Linux only. The claude argv is fixed server-side; sessions always run `claude`, not arbitrary CLIs.
