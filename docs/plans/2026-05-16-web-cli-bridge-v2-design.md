# Web CLI Bridge — v2 Design

Supersedes [`2026-04-30-web-cli-bridge-design.md`](2026-04-30-web-cli-bridge-design.md). The v1 design still describes the core architecture and the original `/run` shape; this doc layers on the features that landed after v1 plus those previously deferred.

## What changed since v1

| Area | v1 | v2 |
|---|---|---|
| Files | `server.py` only | `server.py`, `bridge_app.py`, `setup.py`, `README.md` |
| Per-request timeout | Deferred | Optional `timeout` field on `/run` and `/stream` |
| Stdin | Deferred | Optional `stdin` field on `/run` and `/stream` |
| Streaming output | Deferred | New `POST /stream` SSE endpoint |
| Authentication | Open loopback | Optional bearer token via `WEB_CLI_BRIDGE_TOKEN` |
| Port (server.py) | `argv[1]` | Unchanged |
| Port (bridge_app.py) | Hardcoded `8765` | Config file + env override + menu UI |
| 400 catches | `KeyError, JSONDecodeError, FileNotFoundError, NotADirectoryError` | Adds `ValueError, TypeError` for malformed fields |

Everything is additive. A v1 client that only sends `{ "command": "..." }` to `/run` keeps working unchanged.

## Goals (refined)

- Still the smallest reasonable thing that works.
- Backward compatible with v1 clients.
- Opt-in extras: clients that need timeouts, streaming, stdin, or auth get them without forcing them on anyone else.

## Non-goals

- Authentication beyond a single shared token.
- Streaming **stdin** (only batch stdin is supported — full input is sent with the request).
- Sandboxing, allowlists, env injection, logging, daemonization.

## API

### `POST /run` (buffered, unchanged shape + new optional fields)

Request body:

```json
{
  "command": "git status",
  "cwd": "/optional/path",
  "timeout": 30,
  "stdin": "optional input piped to the command"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `command` | string | yes | Passed to `subprocess.run(shell=True)` |
| `cwd` | string | no | Working directory |
| `timeout` | number | no | Seconds. Omit for no timeout |
| `stdin` | string | no | Sent verbatim on the process's stdin |

Response body (HTTP 200 when the command ran or timed out):

```json
{
  "stdout": "...",
  "stderr": "...",
  "exit_code": 0,
  "timed_out": false
}
```

- `exit_code` is `null` when the command timed out before exiting.
- `timed_out` is always present; `false` for normal completions.
- HTTP layer still reports bridge health only — non-zero exit codes remain HTTP 200.

### `POST /stream` (new, Server-Sent Events)

Request body — same shape as `/run`.

Response: `text/event-stream` with `Connection: close` (no `Content-Length`). The body is a sequence of SSE events terminated when the process exits:

```
event: stdout
data: {"chunk": "hello\n"}

event: stderr
data: {"chunk": "warning: ...\n"}

event: exit
data: {"exit_code": 0, "timed_out": false}
```

- Each `data:` payload is a JSON object. Output chunks ride inside `chunk` so newlines and quotes are JSON-safe.
- `stdout` / `stderr` events arrive as fast as the process produces output; the server uses `select` to multiplex.
- Exactly one `exit` event closes the stream. After it, the server closes the connection.
- Non-UTF-8 bytes are decoded with `errors="replace"` (matches `/run`).
- `timeout` works identically — on timeout the process is killed and a final `exit` event reports `exit_code: null, timed_out: true`.

### `OPTIONS /run`, `OPTIONS /stream`

CORS preflight. 204 + the three v1 headers, plus `Authorization` added to `Access-Control-Allow-Headers` so authenticated browser clients can preflight.

### Error responses

- `400` — JSON parse failure, missing `command`, `cwd` does not exist, or malformed `timeout` / `stdin` field. Now also catches `ValueError` and `TypeError` from coercion / bad field types.
- `401` — only when `WEB_CLI_BRIDGE_TOKEN` is set and the request is missing / has a wrong `Authorization: Bearer <token>` header.
- `405` — `POST` to a path other than `/run` or `/stream`.
- `500` — unexpected server error.
- `501` — methods without a handler (`GET`, `PUT`, …).

`GET` is still deliberately not handled.

## Authentication

The server is open by default — same as v1. If the environment variable `WEB_CLI_BRIDGE_TOKEN` is set when the server starts, every `POST` to `/run` or `/stream` must carry:

```
Authorization: Bearer <token>
```

The comparison uses `hmac.compare_digest`. Wrong / missing token → `401 {"error": "unauthorized"}`. `OPTIONS` is never gated (CORS preflights cannot send `Authorization`).

The menu bar app generates a random token on demand and exposes it through a "Token" submenu (Generate / Copy / Clear). The token is persisted to the config file (below).

## Configuration (menu bar app only)

`server.py` stays argv-driven. The menu bar app adds persistent settings at:

```
~/Library/Application Support/WebCLIBridge/config.json
```

```json
{
  "port": 8765,
  "token": null
}
```

Resolution order on launch:

1. `WEB_CLI_BRIDGE_PORT` env variable (if set, wins).
2. `port` from config file (if present).
3. Default `8765`.

`WEB_CLI_BRIDGE_TOKEN` env behaves the same way for the token.

Menu items:

- **Status** — running / stopped + port.
- **Start** / **Stop** — callbacks become no-ops with user-visible alerts when irrelevant.
- **Change Port…** — opens a `rumps.Window`; saves to config and restarts the server on the new port.
- **Token ▸ Generate** — new random token, persisted, server restarted with it active.
- **Token ▸ Copy** — copies current token to clipboard (only enabled when one is set).
- **Token ▸ Clear** — wipes token from config, restarts server open.
- **About** — shows `CFBundleVersion`.
- **Quit** — graceful stop then `rumps.quit_application()`.

## Streaming implementation notes

- `subprocess.Popen` with `stdout=PIPE, stderr=PIPE, bufsize=0`.
- `select.select` on the two pipe fds; read up to 4 KiB per ready fd; decode each chunk with `errors="replace"`.
- If `timeout` is set, track elapsed time and `proc.kill()` on overrun. After kill, drain whatever the pipes still hold.
- On the HTTP side: omit `Content-Length`, set `Connection: close`, flush after every event so the client sees output immediately.
- Stdin is written once (full string) before reading begins, then the input pipe is closed.

## Security model (revised)

Same loopback-only assumption as v1. The new shared-token mode is enough to defeat untrusted browser tabs that know the port but not the token — useful when running the .app on a multi-user machine. Token in env or in the config file in your home directory; the file is created with mode 0600.

Streaming does not change the threat model: same process, same shell, same user.

## Deferred (still)

- Allowlists / sandboxing.
- Streaming stdin (live input during a long-running command).
- Multi-user / multi-host operation.
- Per-command env injection (clients can still set vars via `VAR=val command` since `shell=True`).

## Files produced

- `server.py` — full bridge incl. streaming and auth.
- `bridge_app.py` — menu bar app with config + token UI.
- `setup.py` — py2app config, version 0.2.0.
- `docs/plans/2026-05-16-web-cli-bridge-v2-design.md` — this doc.
- `README.md` — updated user-facing docs.

The v1 doc stays in place as historical context, with a forward-pointer at the top.
