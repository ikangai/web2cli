# Windows binary — Design

A Windows user pastes one line into PowerShell and ends up with a running tray app:

```powershell
irm https://raw.githubusercontent.com/ikangai/web2cli/main/install.ps1 | iex
```

Direct parallel to the [macOS installer design](2026-05-17-single-line-installer-design.md). This document covers only the Windows-specific deltas.

## Goals

- Zero build dependencies on the user's machine — no Python, pip, PyInstaller.
- Install time ≤ 10 seconds on broadband.
- Re-running the installer upgrades cleanly.
- Failure modes produce a single readable error, not a stack trace.
- Same UX as the macOS app: tray icon, Start/Stop, Change Port, Token Generate/Copy/Clear, About, Quit.

## Non-goals (v1)

- **Code signing.** EV cert is ~$200/yr. SmartScreen will show "Windows protected your PC" on first launch; users click *More info → Run anyway*. Same trust boundary as the `irm | iex` itself.
- **ARM64-native build.** Windows-on-ARM runs x64 under emulation transparently; native ARM64 is a follow-up.
- **MSI / Microsoft Store packaging.** The PowerShell + zip approach is simpler and matches macOS.
- **Auto-update.** Re-run the one-liner.
- **Uninstall flag.** Follow-up.

## Architecture — parallel to macOS

| Piece | macOS | Windows |
|---|---|---|
| Headless server | `server.py` | `server.py` (same file, no change) |
| Tray UI | `bridge_app.py` (`rumps`) | `bridge_app_win.py` (`pystray` + `tkinter`) |
| Bundler config | `setup.py` (`py2app`) | `pyinstaller-win.spec` (`PyInstaller`) |
| Build env | Local Mac (manual) | GitHub Actions (`windows-latest`, triggered by tag push) |
| Release asset | `WebCLIBridge.app.zip` | `WebCLIBridge-windows-x64.zip` |
| Installer | `install.sh` | `install.ps1` |
| One-liner | `curl -fsSL …/install.sh \| sh` | `irm …/install.ps1 \| iex` |
| Install location | `/Applications/WebCLIBridge.app` (sudo) | `%LOCALAPPDATA%\Programs\WebCLIBridge\` (no admin) |
| Quarantine strip | `xattr -dr com.apple.quarantine` | `Unblock-File` |
| Config file | `~/Library/Application Support/WebCLIBridge/config.json` | `%LOCALAPPDATA%\WebCLIBridge\config.json` |

## `bridge_app_win.py` — direct translation of `bridge_app.py`

**Substitutions:**

| `bridge_app.py` | `bridge_app_win.py` |
|---|---|
| `rumps.App` | `pystray.Icon` |
| `rumps.MenuItem` | `pystray.MenuItem` |
| `rumps.Window` (text input) | `tkinter.simpledialog.askinteger / askstring` |
| `rumps.alert` (modal message) | `tkinter.messagebox.showinfo / showerror` |
| `rumps.notification` | Drop. Toast notifications need pywin32/winrt — too heavy. Use modal `showinfo` only where the macOS version blocks (token generation). |
| Title `"⚡"` | PIL-rendered 16×16 bolt polygon, generated at startup (no asset file needed). |
| `subprocess.run(["pbcopy"], …)` | `subprocess.run(["clip"], input=text, encoding="utf-16le", …)` |

**Menu layout — identical:**

```
Status: running :8765 (auth)
─────
Start
Stop
─────
Change Port…
Token ▶
  Generate
  Copy
  Clear
─────
About
Quit
```

**Threading model — identical:** main thread runs `pystray.Icon.run()` (blocks until Quit); HTTP server runs on a worker thread. Menu callbacks fire on a background thread.

**Why not share code with `bridge_app.py`?** ~70% of the lines would be common (config, server lifecycle, token management). Factoring a `bridge_core.py` is a viable v2 refactor, but doing it now would yield a worse interface than two clear files. YAGNI.

## `pyinstaller-win.spec`

Single-file `.exe`, ~30 lines. Critical flags:

- `console=False` — no console window flashes on launch. Analog of macOS `LSUIElement: True`.
- `hiddenimports=['server']` — PyInstaller picks up `from server import Handler`.
- `--onefile` (via spec) — embeds the Python interpreter and all C extensions (pystray, Pillow) in a single .exe. Output: ~25–35 MB.

## `.github/workflows/release-windows.yml`

```yaml
on:
  push:
    tags: ['v*']
  workflow_dispatch:        # manual trigger for testing
```

Steps on `windows-latest`:

1. `actions/checkout@v4`
2. `actions/setup-python@v5` — pin Python **3.12** (stable PyInstaller support; 3.14 has known PyInstaller lag).
3. `pip install pystray Pillow pyinstaller`
4. `pyinstaller --noconfirm pyinstaller-win.spec`
5. `Compress-Archive dist/WebCLIBridge.exe -DestinationPath WebCLIBridge-windows-x64.zip`
6. `gh release create "$TAG" --notes "…" || true; gh release upload "$TAG" WebCLIBridge-windows-x64.zip --clobber`

The `create … || true` makes the workflow idempotent: if the release already exists (because macOS asset was uploaded first), the create is a no-op and the upload succeeds. `--clobber` allows re-runs to overwrite.

**Cost:** Public repos get unlimited GitHub Actions minutes. ~3 minutes per release. Zero ongoing cost.

## `install.ps1` — flow

```
preflight  →  resolve latest release  →  download zip to $env:TEMP
   ↓
stop running process  →  Expand-Archive  →  move to %LOCALAPPDATA%\Programs\WebCLIBridge\
   ↓
Unblock-File  →  create Start Menu shortcut  →  Start-Process  →  success
```

### Preflight

- `$ErrorActionPreference = 'Stop'` — equivalent of `set -e`.
- Refuse with "Windows only" if `$IsWindows -eq $false` (PowerShell Core runs on macOS/Linux too).
- Refuse with "PowerShell 5.1+ required" if `$PSVersionTable.PSVersion -lt [Version]'5.1'`.

### Resolve latest release

```powershell
$release = Invoke-RestMethod 'https://api.github.com/repos/ikangai/web2cli/releases/latest'
$asset = $release.assets | Where-Object { $_.name -eq 'WebCLIBridge-windows-x64.zip' } | Select-Object -First 1
```

No `jq` gymnastics — PowerShell speaks JSON natively. Wrap in `try/catch`; on 404, print "no release published yet" with the releases-page URL.

### Download & install

```powershell
$tmp = New-Item -ItemType Directory -Path "$env:TEMP\web2cli-$([guid]::NewGuid())"
Invoke-WebRequest $asset.browser_download_url -OutFile "$tmp\app.zip"
Expand-Archive "$tmp\app.zip" -DestinationPath $tmp
```

Stop a running instance before replacing files:

```powershell
Get-Process WebCLIBridge -ErrorAction SilentlyContinue | Stop-Process -Force
```

Move into `%LOCALAPPDATA%\Programs\WebCLIBridge\`. Create dir if missing.

### Post-install

- `Unblock-File "$installDir\WebCLIBridge.exe"` — strips the Mark of the Web (Zone.Identifier) so SmartScreen doesn't gate first launch.
- Create Start Menu shortcut via `WScript.Shell` COM object (~4 lines of PS).
- `Start-Process "$installDir\WebCLIBridge.exe"`.
- Print: `Installed WebCLIBridge v<X>. Look for the bolt icon in your system tray.`

### Failure-mode matrix

| Condition | Exit message |
|---|---|
| Not Windows | `error: Windows only` |
| PowerShell < 5.1 | `error: PowerShell 5.1+ required` |
| No release on GitHub | `error: no release published yet. See https://github.com/ikangai/web2cli/releases` |
| No matching asset | `error: release v<X> has no WebCLIBridge-windows-x64.zip asset` |
| Network failure | `error: download failed: <message>` |
| Locked install dir | propagated from `Move-Item` |

## Testing

**From macOS (limited):**

- `brew install powershell` then `pwsh -NoProfile -File install.ps1 -WhatIf` — basic syntax / dry-run lint.
- `python3 -c 'import bridge_app_win'` — will fail at `import pystray` (platform-specific). Not a useful check; needs Windows.
- Push to a feature branch; let the Actions workflow `workflow_dispatch` build and surface PyInstaller failures.

**Needs a Windows machine:**

- Tray icon renders, menu items work, Start/Stop toggles correctly.
- Token Generate / Copy (verify clipboard receives UTF-16 text).
- Config file at `%LOCALAPPDATA%\WebCLIBridge\config.json` persists across restarts.
- `install.ps1` end-to-end: fresh install, upgrade, run-after-install.

If no Windows machine: rely on Actions to build (catches PyInstaller failures + hidden-import gaps), then ask one trusted Windows user to verify before publishing.

## Release flow — combined macOS + Windows

1. Bump version in `setup.py`, `bridge_app.py`, `bridge_app_win.py`, `pyinstaller-win.spec`.
   - *Follow-up:* introduce a single `__version__` in `server.py` imported by all of the above.
2. Build & zip macOS bundle locally (existing flow).
3. `git tag v0.3.0 && git push --tags` — Actions workflow builds Windows asset and attaches to (or creates) the GitHub release.
4. `gh release create v0.3.0 WebCLIBridge.app.zip --notes-file CHANGELOG.md` — adds macOS asset (workflow is idempotent against an already-created release).
5. Verify both one-liners against the new release.

## Acceptance criteria

- Windows user can paste one line and have a working tray app within ~10 seconds.
- Re-running the line upgrades cleanly without errors.
- All failure modes in the matrix produce readable single-line errors.
- Build runs unattended on every tag push; the user never has to touch a Windows machine for normal releases.

## Follow-ups (post-v1)

- Single source of truth for the version string (`server.__version__`).
- Refactor `bridge_app.py` + `bridge_app_win.py` into a shared `bridge_core.py` if the duplication starts to hurt.
- Sign & notarize on both platforms → remove `xattr -dr` and `Unblock-File` steps.
- Native ARM64 Windows build.
- Linux install path (drop `server.py` on `$PATH`, optional `systemd --user` unit).
- `--uninstall` flag for both installers.
