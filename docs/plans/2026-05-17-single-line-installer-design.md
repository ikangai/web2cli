# Single-line installer — Design

A user pastes one line into Terminal and ends up with a running menu bar app:

```sh
curl -fsSL https://raw.githubusercontent.com/ikangai/web2cli/main/install.sh | sh
```

## Goals

- Zero build dependencies on the user's machine — no Python, pip, py2app, or Xcode Command Line Tools.
- Install time ≤ 10 seconds on a typical broadband connection.
- Re-running the installer upgrades cleanly without errors.
- Failure modes (wrong OS, no network, no release) produce a single readable error, not a stack trace.

## Non-goals (v1)

- **Code signing / notarization.** A real Gatekeeper fix needs an Apple Developer ID (~$100/yr). For v1 we strip the `com.apple.quarantine` xattr after install, which is the same trust boundary as the `curl | sh` itself.
- **Linux / Windows.** No `.app` bundle on those platforms; a separate install path would be needed.
- **Checksum verification beyond HTTPS.**
- **Auto-update.** Re-run the one-liner to upgrade.
- **Uninstall.** Adding a `--uninstall` flag is cheap follow-up work but not required for v1.

## Architecture — three pieces

| Piece | Who does it | When |
|---|---|---|
| Pre-built `WebCLIBridge.app.zip` as a GitHub release asset | Maintainer | Once per version |
| `install.sh` at the repo root, served via `raw.githubusercontent.com` | Maintainer | Once, committed to repo |
| README install snippet | Maintainer | Once, committed to repo |

The installer itself stays static — it always asks the GitHub API for the latest release, so new versions don't require a new `install.sh`.

## `install.sh` — flow

```
preflight  →  resolve latest version  →  download zip to /tmp
   ↓
quit running app (best-effort)  →  unzip  →  sudo mv to /Applications
   ↓
xattr -dr com.apple.quarantine  →  open -a WebCLIBridge  →  success
```

### Preflight

- `uname -s` must be `Darwin` — bail with "macOS only" otherwise.
- Required tools (`curl`, `unzip`, `xattr`, `mktemp`, `open`) all ship with macOS; check anyway.
- Reconnect stdin to `/dev/tty` so `sudo` can prompt for a password (the `curl | sh` pipe occupies stdin otherwise — standard rustup/Homebrew pattern):

  ```sh
  if [ ! -t 0 ] && [ -r /dev/tty ]; then
    exec </dev/tty
  fi
  ```

### Resolve latest release

```sh
curl -fsSL https://api.github.com/repos/ikangai/web2cli/releases/latest
```

Parse with `grep` + `sed` (no `jq` dependency) to find the `browser_download_url` for an asset named `WebCLIBridge.app.zip`. If no matching asset exists, fail with a pointer to https://github.com/ikangai/web2cli/releases.

### Download & stage

```sh
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$DOWNLOAD_URL" -o "$TMP/WebCLIBridge.app.zip"
unzip -q "$TMP/WebCLIBridge.app.zip" -d "$TMP"
```

### Replace existing install

If `/Applications/WebCLIBridge.app` already exists:

1. Best-effort quit: `osascript -e 'tell application "WebCLIBridge" to quit'` (don't fail the script if this errors — the app may not be running).
2. `sudo rm -rf /Applications/WebCLIBridge.app`.
3. `sudo mv "$TMP/WebCLIBridge.app" /Applications/`.

### Post-install

- `sudo xattr -dr com.apple.quarantine /Applications/WebCLIBridge.app` — strips the Gatekeeper quarantine bit. Without this, an unsigned bundle downloaded over the network hits the "downloaded from internet, can't be opened" dialog.
- `open -a WebCLIBridge`.
- Print: `Installed WebCLIBridge v<X>. Look for ⚡ in your menu bar.`

### Shell discipline

- `#!/bin/sh` + `set -eu` — POSIX, no bashisms.
- Color output via `tput` when stdout is a TTY; plain otherwise.
- Every error path prints a single line beginning with `error:` and exits non-zero.

## Release-side work — once per version

This is what the maintainer does to ship a new release that `install.sh` can find.

1. **Build the bundle:**
   ```sh
   python3 -m venv .venv
   .venv/bin/pip install py2app rumps
   .venv/bin/python setup.py py2app
   ```

2. **Confirm universal2** so the bundle runs on both Apple Silicon and Intel:
   ```sh
   file dist/WebCLIBridge.app/Contents/MacOS/WebCLIBridge
   ```
   Expected: `Mach-O universal binary with 2 architectures: x86_64, arm64`. If single-arch, ship it but document the limitation in release notes — users on the other arch will need to rebuild locally.

3. **Zip with `ditto`** (preserves bundle symlinks; regular `zip` mangles them):
   ```sh
   ditto -c -k --keepParent dist/WebCLIBridge.app WebCLIBridge.app.zip
   ```

4. **Tag & release:**
   ```sh
   git tag v0.2.0 && git push --tags
   gh release create v0.2.0 WebCLIBridge.app.zip --notes-file CHANGELOG.md
   ```

The asset name `WebCLIBridge.app.zip` is fixed (not versioned in the filename), so the installer doesn't have to construct version-specific URLs — it just picks the asset by name from the latest release JSON.

## Failure-mode matrix

| Condition | Exit message |
|---|---|
| Not macOS | `error: macOS only (detected: <uname>)` |
| Missing `curl` / `unzip` / `xattr` | `error: missing required tool: <name>` |
| No releases on GitHub yet | `error: no release found. See https://github.com/ikangai/web2cli/releases` |
| Latest release has no `.app.zip` asset | `error: release v<X> has no WebCLIBridge.app.zip asset` |
| Network failure on download | `error: download failed: <curl exit code>` |
| `sudo` denied | propagated from `sudo` (it prints its own message) |

## Testing

- **Local dry-run before publishing:** serve `install.sh` from `python3 -m http.server` and run it from another shell — verifies the script logic without needing a public commit.
- **Fresh install:** test on a Mac that has never had `WebCLIBridge.app` installed. Verify menu bar icon appears.
- **Reinstall:** run twice; second run should quit the running app, replace the bundle, and relaunch.
- **Failure paths:** force each row of the matrix above (rename `WebCLIBridge.app.zip` in the release to break asset lookup; disconnect network; etc.) and confirm the message is readable.

## Acceptance criteria

- A new user can paste one line and have a working menu bar app within ~10 seconds.
- Re-running the line upgrades cleanly without errors.
- All failure modes in the matrix produce readable single-line errors, not stack traces.

## Follow-ups (post-v1)

- Sign and notarize the bundle → remove the `xattr` step → real Gatekeeper compatibility.
- `--uninstall` flag.
- Linux install path (drop `server.py` on `$PATH`, optional `systemd --user` unit).
- Optional `pip install web2cli` for headless users who only want the server.
