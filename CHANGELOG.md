# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-05-17

Adds Windows support and a one-line installer on both platforms. No changes to the wire protocol — every v0.2.0 client keeps working.

### Added
- **`bridge_app_win.py`** — Windows tray app. Same menu and behavior as the macOS app: Start/Stop, Change Port, Token Generate/Copy/Clear, About, Quit. Uses [`pystray`](https://pypi.org/project/pystray/) for the tray icon and `tkinter` (stdlib) for dialogs. Config at `%LOCALAPPDATA%\WebCLIBridge\config.json`.
- **`pyinstaller-win.spec`** — PyInstaller config that builds `dist\WebCLIBridge.exe` (single-file, no console window).
- **`.github/workflows/release-windows.yml`** — GitHub Actions workflow that builds the Windows `.exe` on every `v*` tag push and attaches `WebCLIBridge-windows-x64.zip` to the release. Manual `workflow_dispatch` trigger available for testing.
- **`install.ps1`** — PowerShell installer. One-liner: `irm https://raw.githubusercontent.com/ikangai/web2cli/main/install.ps1 | iex`. Resolves the latest release, downloads the asset, installs to `%LOCALAPPDATA%\Programs\WebCLIBridge\`, strips the Mark of the Web with `Unblock-File`, creates a Start Menu shortcut, and launches the app.
- README install section now covers both platforms.
- Design doc at `docs/plans/2026-05-17-windows-binary-design.md`.

### Changed
- `setup.py`: `CFBundleVersion` / `CFBundleShortVersionString` bumped to `0.3.0`.
- `bridge_app.py` / `bridge_app_win.py`: `VERSION = "0.3.0"`.

## [0.2.0] — 2026-05-16

Closes every "deferred" item from the v1 design and lands the menu bar app's configuration model. Fully backward compatible — v1 clients that only send `{ "command": "..." }` to `/run` keep working unchanged.

### Added
- **`POST /stream`** — Server-Sent Events endpoint. Emits one `stdout` / `stderr` event per chunk and a final `exit` event. Concurrent stdout/stderr multiplexed via `select`.
- **Per-request `timeout`** field on `/run` and `/stream`. On overrun: process is killed, partial output is returned, `exit_code` is `null`, `timed_out: true`.
- **Per-request `stdin`** field — string sent verbatim on the process's stdin (batch, not streaming).
- **Optional bearer-token auth** via `WEB_CLI_BRIDGE_TOKEN`. Required on `/run` and `/stream` when set; `OPTIONS` is always allowed. Comparison uses `hmac.compare_digest`.
- **`timed_out` field** on every `/run` response (`false` for normal completions).
- Menu bar app: persistent config at `~/Library/Application Support/WebCLIBridge/config.json` (port + token, mode `0600`).
- Menu bar app: `WEB_CLI_BRIDGE_PORT` and `WEB_CLI_BRIDGE_TOKEN` env overrides.
- Menu bar app: **Change Port…** dialog (validates 1–65535, restarts the server in place).
- Menu bar app: **Token ▸ Generate / Copy / Clear** submenu, using `secrets.token_urlsafe(32)` and `pbcopy`.
- Menu bar app: **About** item showing version, address, auth state, and config path.
- v2 design doc at `docs/plans/2026-05-16-web-cli-bridge-v2-design.md`.

### Changed
- CORS preflight now allows `Authorization` in `Access-Control-Allow-Headers`.
- `400` errors now also catch `TypeError` for malformed `command` / `cwd` / `timeout` / `stdin` field types.
- Menu bar app status text shows the active port and an `(auth)` suffix when a token is set.
- Menu bar app gives visible feedback when **Start** / **Stop** are clicked while the server is already in that state (previously a silent no-op).
- README rewritten to cover the new endpoints, fields, auth, and config model.
- `setup.py`: `CFBundleVersion` / `CFBundleShortVersionString` bumped to `0.2.0`.

### Documentation
- v1 design doc gets a "superseded by v2" banner and a footnote noting that the 400 list also catches `ValueError` (for missing / non-numeric `Content-Length`).

### Still deferred
- Streaming **stdin** (live typing during a long-running command).
- Allowlist / sandboxing of executable commands.
- Multi-user / multi-host operation.

## [0.1.0] — 2026-04-30

Initial release.

### Added
- `POST /run` — buffered shell command execution returning `{stdout, stderr, exit_code}`.
- `OPTIONS /run` — CORS preflight.
- 405 / 500 / 501 error responses; `GET` deliberately unhandled.
- macOS menu bar app (`bridge_app.py`) with Start / Stop / Quit.
- `py2app` configuration to bundle a standalone `.app`.

[0.3.0]: https://github.com/ikangai/web2cli/releases/tag/v0.3.0
[0.2.0]: https://github.com/ikangai/web2cli/releases/tag/v0.2.0
[0.1.0]: https://github.com/ikangai/web2cli/commit/00dd574
