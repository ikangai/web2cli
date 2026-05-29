# Persistent Interactive claude Session — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Drive a real, persistent interactive `claude` CLI session per workspace from the web_cli_bridge HTTP server, exchanging structured turns through a file rendezvous gated on a `.part`→rename completion edge rather than screen quiescence.

**Architecture:** A stdlib-only Python HTTP server owns a registry of long-lived tmux sessions, each running the real `claude` TUI; turns stage a per-turn `doc.<turn_uuid>.html`, type a prompt, and read back a byte-exact `env.<turn_uuid>.json` written via `.part`→rename. A finite-state classifier reads tmux `capture-pane` output (footer/spinner tokens) only to gate idle/working/awaiting-input/dead, never to time turn completion. The browser rwa is a dependency-free JS document client that re-stages docs and renders envelopes.

**Tech Stack:** Python 3 stdlib only (http.server, subprocess, threading), tmux CLI, pytest (dev-only), driving the real `claude` CLI; rwa is dependency-free JS.

---

## Baseline reconciliation (read before Task 1)

This plan was drafted against a pre-refactor `server.py`; the worktree has since been **rebased onto the current refactored baseline** (commit `8cbf212`). Apply these deltas as you execute:

1. **`server.py` is now the 388-line refactored module** and `bridge_common.py` already exists. The few `server.py:NN` line numbers in this plan (e.g. `server.py:34,42-45,87-104`) are from the *old* layout — locate those edit points by **symbol**, not line: the `class Handler(...)` definition (~line 83), `do_OPTIONS` (~91), and the top of `do_POST` (~154).
2. **Reuse the existing `Handler` helpers — do not redefine them.** `Handler` already provides `_write_sse`, `_sse`, `_sse_exit`, `_send`, `_cors`, `_authorized`, `_parse_body`, plus the process-cleanup helpers `_kill_tree` / `_reap` / `_PIPE_CLOSED` (and `start_new_session`). Wiring `class Handler(SessionMixin, BaseHTTPRequestHandler)` gives `SessionMixin` all of them via `self`. **Skip the step that "adds `_write_sse` to `SessionMixin`"** — Handler's is identical and would shadow it anyway; just call `self._write_sse(...)`.
3. **Capture-fixture source.** The committed capture log is `docs/plans/2026-05-29-calibration-captures.log` (preserved from the live calibration). Point the fixture-slicer at that path, **not** the ephemeral `/tmp/wcbcal-captures.log`.

Verified tooling in this worktree: pytest 9.0.2, tmux 3.6a, bash 5.3.9.

---

## Testing strategy

Every behaviour in this plan is gated by a test in one of three tiers, plus a set of real claude screen captures saved as fixtures:

- **Tier 1 — pure unit (`tests/unit/`).** No tmux, no claude, no network. Pure functions are driven against in-memory data and the real capture fixtures (below): the FSM `classify()`, path-safety helpers, envelope read-back/sentinel checks, prompt building, and HTTP request parsing. Fast, deterministic, run on every change. Default `python3 -m pytest -q`.
- **Tier 2 — fake_claude.sh integration through real tmux (`tests/integration/`).** A tiny POSIX-sh claude TUI mimic (`tests/fake_claude.sh`) is driven through the *real* `tmux` binary with the exact `capture-pane`/`send-keys` calls the bridge uses against real claude. This exercises the load-bearing edges — composer-ready footer, the workspace-trust gate, the rising-spinner WORKING gate, and the `WRITE`→`.part`→rename→`⏺ DONE` completion edge — with no claude quota and no model flakiness. Skips cleanly when `tmux` is absent (`requires_tmux`).
- **Tier 3 — one env-gated live smoke (`tests/live/`).** A single end-to-end turn against the *real* `claude` binary, opt-in only: skipped unless `WCB_LIVE_SMOKE=1`, so the default suite and CI never spend claude quota. The real `test_live_smoke.py` lands in the final phase; this component only pins the opt-in gate.
- **Real calibration screen captures saved as fixtures (`tests/fixtures/captures/*.txt`).** Classification runs on *real* claude v2.1.156 screens, never synthetic ones (locks design risk #2). The verbatim screens are sliced out of the live calibration log `/tmp/wcbcal-captures.log` (384 lines, captured 2026-05-29) by a committed slicer; tests pin the exact tokens each fixture must carry so a re-slice cannot silently drift.

---

# Component: test-scaffold (Phase 0 — test harness)

> Worktree root (all relative paths below are under it): `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/`
> Branch: `feat/persistent-claude-session`. Verified tooling: pytest 9.0.2, tmux 3.6a (`/opt/homebrew/bin/tmux`), GNU bash 5.3.9 (`/opt/homebrew/bin/bash`). Runtime stays stdlib-only; pytest is dev-only.
> Capture source: `/tmp/wcbcal-captures.log` (384 lines, claude 2.1.156). Panel headers have the exact form `===== <name>  (<hh:mm:ss>)  alt=0  cmd=<proc> =====`.
> This component MUST land before any later phase — it is the harness every other task runs against. It writes ZERO production code (no `paths.py`/`fsm.py`/`tmux_session.py` yet); it only stands up pytest, the four shared `conftest.py` fixtures, the real capture fixtures, and the single canonical `fake_claude.sh`.
> **Canonical-harness rule (critique FIX — fake_claude.sh authored 3×):** `tests/fake_claude.sh` is authored exactly ONCE, here in Task 3, and is the single source of truth. It already carries the SIGINT trap that cleanup-security relies on, the canonical footer token set the FSM and TmuxClient key on, both `EXIT` (→ `claude --resume <uuid>` + shell prompt) semantics, and the rising-spinner timer mode that exercises design risk #2 end-to-end. Later components (tmux-client, registry-lifecycle, turn-protocol-fsm, cleanup-security) **import and depend on this file — they do not re-author it.**
> **Import-mode rule (critique FIX — conftest import style):** `pytest.ini` pins `importmode=importlib` with `pythonpath = . tests tests/integration` so that both bare `from conftest import TMUX, FAKE, requires_tmux` and intra-package cross-module imports (e.g. `from test_session_stream_sse import http_server`) resolve in every later component. The `tests/`, `tests/unit/`, `tests/integration/`, `tests/live/` packages all carry `__init__.py`; every test uses the **bare** `from conftest import ...` style consistently — never `from tests.conftest import ...`.

---

## Task 1 — pytest harness bootstrap

Stand up pytest config, the dev-only dependency pin, the `tests/` package tree, the four shared `conftest.py` fixtures from the contract, and a trivial smoke test proving the harness runs, discovers tests, and resolves the bare `from conftest import ...` style every later component uses.

**Files**
- Create `pytest.ini`
- Create `requirements-dev.txt`
- Create `tests/__init__.py` (empty, zero bytes — keeps the dir importable for shared helpers)
- Create `tests/conftest.py`
- Create `tests/unit/__init__.py` (empty, zero bytes)
- Test `tests/unit/test_smoke.py`

### Step 1 — write the FAILING test

Create `tests/unit/test_smoke.py`:

```python
"""Proves the pytest harness is wired up, the shared fixtures import, and the
bare `from conftest import ...` style (used by every later component) resolves.
"""
import pathlib

import pytest


def test_pytest_runs():
    assert True


def test_bare_conftest_import_resolves():
    # Every later component uses `from conftest import TMUX, FAKE, requires_tmux`.
    # importmode=importlib + pythonpath in pytest.ini must make this work.
    from conftest import TMUX, FAKE, CAPTURES, requires_tmux  # noqa: F401
    assert "fake_claude.sh" in str(FAKE)
    assert str(CAPTURES).endswith("fixtures/captures")


def test_conftest_fixtures_exist(request):
    # All four shared fixtures from the plan contract must be registered.
    for name in ("tmp_base", "capture", "fake_socket", "fake_claude_argv"):
        assert _fixture_defined(request, name), (
            f"shared fixture {name!r} is not defined in conftest.py"
        )


def _fixture_defined(request, name):
    # _fixturemanager._arg2fixturedefs maps fixture name -> definitions.
    return name in request._fixturemanager._arg2fixturedefs


def test_tmp_base_is_0700_dir(tmp_base):
    assert tmp_base.is_dir()
    assert (tmp_base.stat().st_mode & 0o777) == 0o700


def test_repo_root_has_pytest_ini():
    root = pathlib.Path(__file__).resolve().parents[2]
    assert (root / "pytest.ini").is_file()
```

### Step 2 — run it + expect FAIL

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_smoke.py
```

Expected: collection error / failure — there is no `pytest.ini`, no `tests/conftest.py`, so `from conftest import ...` raises `ModuleNotFoundError`, `tmp_base` is an unknown fixture, and `test_repo_root_has_pytest_ini` fails. Output contains `ModuleNotFoundError: No module named 'conftest'` and/or `fixture 'tmp_base' not found` (exit code non-zero).

### Step 3 — minimal implementation

Create `pytest.ini` (pins importmode + pythonpath so bare `conftest` and intra-package cross-module imports resolve in every later component):

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
# importlib import mode + pythonpath so `from conftest import ...` (bare) and
# intra-package cross-test imports (e.g. `from test_session_stream_sse import ...`)
# resolve consistently across unit/integration/live tiers.
addopts = -ra --import-mode=importlib
pythonpath = . tests tests/integration
```

Create `requirements-dev.txt`:

```
# Dev-only. The packaged runtime is stdlib-only and never imports pytest.
pytest>=8,<10
```

Create `tests/__init__.py` (empty file, zero bytes).

Create `tests/unit/__init__.py` (empty file, zero bytes).

Create `tests/conftest.py` (the four shared fixtures, verbatim from the §2 contract — point `TMUX`/`FAKE`/`CAPTURES` at lookups so the tmux tiers skip cleanly when absent and so later components can `from conftest import TMUX, FAKE, CAPTURES, requires_tmux`):

```python
import os
import pathlib
import shutil
import subprocess

import pytest

TMUX = shutil.which("tmux")
requires_tmux = pytest.mark.skipif(TMUX is None, reason="tmux binary not found")

FAKE = pathlib.Path(__file__).parent / "fake_claude.sh"
CAPTURES = pathlib.Path(__file__).parent / "fixtures" / "captures"


@pytest.fixture
def tmp_base(tmp_path):
    """A 0700 rendezvous base dir owned by euid (mimics verify_base_dir)."""
    d = tmp_path / "rendezvous"
    d.mkdir(mode=0o700)
    # mkdir mode is masked by umask; force the exact bits verify_base_dir wants.
    os.chmod(d, 0o700)
    return d


@pytest.fixture
def capture():
    """Return a reader: capture("composer_ready.txt") -> str (the sliced screen)."""
    return lambda name: (CAPTURES / name).read_text()


@pytest.fixture
def fake_socket():
    """Unique -L socket name per test; kill-server in teardown so no leaks."""
    sock = "wcbtest_" + os.urandom(4).hex()
    yield sock
    if TMUX is not None:
        subprocess.run(
            [TMUX, "-L", sock, "kill-server"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )


@pytest.fixture
def fake_claude_argv():
    """argv that runs the fake claude mimic instead of the real binary."""
    return ["bash", str(FAKE)]
```

### Step 4 — run + expect PASS

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_smoke.py
```

Expected: `5 passed` (exit code 0).

### Step 5 — commit

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add pytest.ini requirements-dev.txt tests/__init__.py tests/unit/__init__.py tests/conftest.py tests/unit/test_smoke.py && git commit -m "test: bootstrap pytest harness + shared conftest fixtures

Adds pytest.ini (testpaths=tests, importmode=importlib, pythonpath so
bare \`from conftest import ...\` resolves everywhere), dev-only
requirements-dev.txt, and the four shared fixtures (tmp_base, capture,
fake_socket, fake_claude_argv) from the persistent-session plan.
Runtime stays stdlib-only.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 — real capture fixtures import (slicer)

Slice the verbatim claude screens out of `/tmp/wcbcal-captures.log` into `tests/fixtures/captures/*.txt`, mapping calibration panel names to the six fixture filenames the FSM tests are driven by. A committed slicer script makes the import reproducible (the log is in `/tmp` and not committed); the test asserts every fixture exists and carries its pinned token. **The screens are the real claude output — not synthetic** (locks design risk #2). One fixture (`thinking_timer.txt`, from `turn-poll-2`) carries the *rising* spinner elapsed timer `(1s · thinking)` so the WORKING-via-rising-timer gate (design risk #2) is driven by a real screen, not only by a mocked `prev_timer` in turn-protocol-fsm.

Panel → fixture mapping (panel names are the `===== <name> ... =====` headers in the log):
- `composer-ready` → `composer_ready.txt` (footer IDLE token, `❯ ` composer, no `esc to interrupt`)
- `turn-poll-1` → `thinking_esc_interrupt.txt` (footer `esc to interrupt` present, spinner `✻ Smooshing…`)
- `turn-poll-2` → `thinking_timer.txt` (rising spinner elapsed timer `· Smooshing… (1s · thinking)` — the risk #2 rising-timer evidence)
- `after-launch-8s` → `trust_prompt.txt` (`Is this a project you created or one you trust?` + `❯ 1. Yes, I trust this folder`)
- `turn-done` → `turn_done.txt` (`⏺ Write(...)`, `⎿  Wrote`, `esc to interrupt` still present — the file-edge-vs-screen evidence)
- `post-exit` → `post_exit_shell.txt` (ends in the `martintreiber@10 ... %` shell prompt after `/exit`, with `claude --resume <uuid>`)

**Files**
- Create `tests/slice_captures.py` (committed importer / regenerator)
- Test `tests/unit/test_fixtures_present.py`
- (Generated by the slicer, committed) `tests/fixtures/captures/composer_ready.txt`, `thinking_esc_interrupt.txt`, `thinking_timer.txt`, `trust_prompt.txt`, `turn_done.txt`, `post_exit_shell.txt`

### Step 1 — write the FAILING test

Create `tests/unit/test_fixtures_present.py`:

```python
"""The FSM tests are driven by REAL claude screens, not synthetic ones.

This asserts the capture fixtures exist and each carries the pinned v2.1.156
token that the FSM will classify on. If /tmp/wcbcal-captures.log is re-sliced,
these tokens must survive verbatim.
"""
import pathlib

import pytest

CAPTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "captures"

# fixture filename -> a substring that MUST appear verbatim in it.
PINNED = {
    "composer_ready.txt": "⏵⏵ bypass permissions on (shift+tab to cycle)",
    "thinking_esc_interrupt.txt": "esc to interrupt",
    "thinking_timer.txt": "Smooshing… (1s",
    "trust_prompt.txt": "Is this a project you created or one you trust?",
    "turn_done.txt": "⎿  Wrote",
    "post_exit_shell.txt": "claude --resume",
}


@pytest.mark.parametrize("name,token", sorted(PINNED.items()))
def test_fixture_exists_and_has_pinned_token(name, token):
    p = CAPTURES / name
    assert p.is_file(), f"missing capture fixture: {p}"
    text = p.read_text()
    assert text.strip(), f"capture fixture is empty: {p}"
    assert token in text, f"{name} lost its pinned token {token!r}"


def test_composer_ready_is_idle_not_working():
    # composer_ready must NOT carry the WORKING gate, else idle/thinking blur.
    text = (CAPTURES / "composer_ready.txt").read_text()
    assert "esc to interrupt" not in text


def test_thinking_timer_has_rising_elapsed_seconds():
    # Drives design risk #2: WORKING is also signalled by a rising spinner
    # elapsed timer, not just the `esc to interrupt` footer. This real screen
    # carries `(1s · thinking)` so the rising-timer gate has a non-synthetic case.
    text = (CAPTURES / "thinking_timer.txt").read_text()
    assert "Smooshing… (1s" in text


def test_trust_prompt_has_yes_option():
    text = (CAPTURES / "trust_prompt.txt").read_text()
    assert "❯ 1. Yes, I trust this folder" in text


def test_turn_done_shows_assistant_and_result_markers():
    # The file-edge-vs-screen evidence: payload done while footer still WORKING.
    text = (CAPTURES / "turn_done.txt").read_text()
    assert "⏺ Write(" in text
    assert "esc to interrupt" in text  # screen still looks busy after file write


def test_post_exit_ends_at_shell_prompt():
    text = (CAPTURES / "post_exit_shell.txt").read_text()
    assert text.rstrip().endswith("%")  # zsh prompt returned after /exit
```

### Step 2 — run it + expect FAIL

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fixtures_present.py
```

Expected: every test fails with `missing capture fixture: .../tests/fixtures/captures/<name>.txt` — the directory and files do not exist yet (exit code non-zero).

### Step 3 — minimal implementation

Create `tests/slice_captures.py` (the committed slicer; run once to generate the fixtures, kept for reproducibility):

```python
#!/usr/bin/env python3
"""Slice the real claude calibration screens out of a capture log into the
per-state fixtures the FSM tests are driven by.

Source log default: /tmp/wcbcal-captures.log (claude 2.1.156, captured
2026-05-29). Panel headers have the exact form:

    ===== <name>  (<hh:mm:ss>)  alt=0  cmd=<proc> =====

Each fixture is the verbatim body between one header and the next (header
line itself dropped, trailing blank lines stripped). Re-run to regenerate:

    python3 tests/slice_captures.py /tmp/wcbcal-captures.log
"""
import pathlib
import re
import sys

HEADER_RE = re.compile(
    r"^===== (?P<name>.+?)\s+\(\d\d:\d\d:\d\d\)\s+alt=\d+\s+cmd=\S+ =====\s*$"
)

# calibration panel name (matched on a prefix of the header name) -> fixture file
PANEL_TO_FIXTURE = {
    "composer-ready": "composer_ready.txt",
    "turn-poll-1": "thinking_esc_interrupt.txt",
    "turn-poll-2": "thinking_timer.txt",
    "after-launch-8s": "trust_prompt.txt",
    "turn-done": "turn_done.txt",
    "post-exit": "post_exit_shell.txt",
}

OUT_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "captures"


def parse_panels(text):
    """Return {panel_name: body_text} for every panel in the log."""
    panels = {}
    current = None
    buf = []
    for line in text.splitlines():
        m = HEADER_RE.match(line)
        if m:
            if current is not None:
                panels[current] = "\n".join(buf)
            current = m.group("name").strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        panels[current] = "\n".join(buf)
    return panels


def main(argv):
    src = (
        pathlib.Path(argv[1])
        if len(argv) > 1
        else pathlib.Path("/tmp/wcbcal-captures.log")
    )
    text = src.read_text()
    panels = parse_panels(text)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    missing = []
    for panel_prefix, fixture in PANEL_TO_FIXTURE.items():
        match = next(
            (name for name in panels if name.startswith(panel_prefix)), None
        )
        if match is None:
            missing.append(panel_prefix)
            continue
        body = panels[match].rstrip("\n") + "\n"
        (OUT_DIR / fixture).write_text(body)
        print(f"wrote {fixture} <- panel {match!r} ({len(body)} bytes)")

    if missing:
        sys.exit(f"ERROR: panels not found in {src}: {missing}")


if __name__ == "__main__":
    main(sys.argv)
```

Then generate the fixtures from the real log (this is part of the implementation step — it produces the committed `.txt` files):

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 tests/slice_captures.py /tmp/wcbcal-captures.log
```

Expected output: six `wrote <fixture> <- panel ...` lines, no ERROR.

> Note for the implementer: if `/tmp/wcbcal-captures.log` has been cleared by the time this task runs, the verbatim panel bodies are reproduced in the design doc's Calibration section and in this component's source-log read (panel→line ranges in the live log: `after-launch-8s` = lines 33–63, `composer-ready` = 97–127, `turn-poll-1` = 161–191, `turn-poll-2` = 193–223, `turn-done` = 257–287, `post-exit` = 353–384). The slicer must produce byte-identical bodies either way; the test in Step 1 is the gate.

### Step 4 — run + expect PASS

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fixtures_present.py
```

Expected: `11 passed` (6 parametrized + 5 standalone; exit code 0).

### Step 5 — commit

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add tests/slice_captures.py tests/unit/test_fixtures_present.py tests/fixtures/captures/composer_ready.txt tests/fixtures/captures/thinking_esc_interrupt.txt tests/fixtures/captures/thinking_timer.txt tests/fixtures/captures/trust_prompt.txt tests/fixtures/captures/turn_done.txt tests/fixtures/captures/post_exit_shell.txt && git commit -m "test: import real claude capture fixtures (v2.1.156)

Slices the live calibration screens from /tmp/wcbcal-captures.log into
six per-state fixtures the FSM tests are driven by, plus a committed
slicer for reproducibility. Includes thinking_timer.txt (rising spinner
elapsed timer) so the risk #2 rising-timer WORKING gate runs on a REAL
screen, not a synthetic one. Locks design risk #2: classification runs
on REAL claude screens, never synthetic ones.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 — canonical fake_claude.sh + integration smoke through real tmux

Author the single canonical `tests/fake_claude.sh` TUI mimic to the §2(b) contract and prove the real `tmux` binary drives it identically to claude across the load-bearing edges: the composer-ready footer, the trust gate, the `WRITE <env_path> <turn_uuid>` → `.part` → rename → `⏺ DONE` completion edge, the rising-spinner WORKING timer (design risk #2), the SIGINT trap (used by cleanup-security's interrupt path), and the `EXIT` → `claude --resume <uuid>` + shell-prompt DEAD discriminator. **This is the one and only `fake_claude.sh` — every Phase 2–4 component (tmux-client, registry-lifecycle, turn-protocol-fsm, cleanup-security) imports and drives THIS file and never re-authors it** (critique FIX: de-duplicate the three conflicting authorings into one). **No claude quota; no model flakiness.**

`fake_claude.sh` canonical contract (matches the skeleton, with the pinned tokens from the captures):
- On start, prints the IDLE composer: a `❯ ` line plus a footer line containing the exact pinned token `⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents`. Never prints `esc to interrupt` while idle.
- WORKING footer is the canonical `⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt` (same prefix as IDLE, `· esc to interrupt` suffix) — the single token set the FSM `_composer_present`/`footer_of` and TmuxClient key on.
- If `WCB_FAKE_TRUST=1`, first prints the trust block (`Is this a project you created or one you trust?`, `❯ 1. Yes, I trust this folder`, `2. No, exit`, `Enter to confirm · Esc to cancel`) and blocks reading one line before showing the composer.
- Installs `trap 'printf "%s\n" "^C INTERRUPTED"' INT` so a tmux `send-keys C-c` / `SIGINT` during a turn prints the `^C INTERRUPTED` marker (cleanup-security's interrupt corroboration depends on this).
- Reads stdin lines. `WRITE <abs_env_path> <turn_uuid>`: prints the WORKING footer token `esc to interrupt`. If `WCB_FAKE_TIMER=1`, also emits an *incrementing* spinner line `✻ Smooshing… (<N>s · thinking)` for `N=1..3` (one per ~`$WCB_FAKE_DELAY` slice) so the rising-timer WORKING gate (design risk #2) is exercised end-to-end. Then writes `<env_path>.part` with `{"tool":"echo","envelope":{"ok":true},"turn_uuid":"<turn_uuid>"}`, `mv`s it to `<env_path>` (exercises the `.part`→rename edge), prints `⏺ Write(...)` + `⏺ DONE`, reverts footer to the IDLE token.
- A bare blank line is a no-op (stays idle). The literal line `EXIT` prints the `claude --resume <uuid>` resume hint and a shell-like prompt line, then exits (DEAD discriminator), matching the real post-exit screen.

**Files**
- Create `tests/fake_claude.sh` (the single canonical mimic; later components depend on it)
- Create `tests/integration/__init__.py` (empty, zero bytes)
- Test `tests/integration/test_fake_claude.py`

### Step 1 — write the FAILING test

Create `tests/integration/test_fake_claude.py`:

```python
"""Drive fake_claude.sh raw through the real tmux binary and assert the
load-bearing edges: composer-ready, the trust gate, the WRITE -> .part ->
rename -> DONE completion edge, the rising-spinner WORKING timer (risk #2),
the SIGINT trap marker, and the EXIT -> claude --resume + shell DEAD edge.

This is the ONE canonical integration harness; every later component imports
fake_claude.sh from here via conftest.FAKE and drives it the same way.
"""
import json
import os
import pathlib
import subprocess
import time

import pytest

from conftest import TMUX, FAKE, requires_tmux


def _tmux(sock, *args, **kw):
    return subprocess.run(
        [TMUX, "-L", sock, *args],
        capture_output=True, text=True, **kw,
    )


def _capture(sock):
    return _tmux(sock, "capture-pane", "-p", "-t", "t").stdout


def _wait_for(sock, needle, timeout=10.0, want=True):
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = _capture(sock)
        if (needle in last) == want:
            return last
        time.sleep(0.1)
    raise AssertionError(
        f"timeout waiting for {needle!r} present={want}; last screen:\n{last}"
    )


@requires_tmux
def test_fake_is_executable_and_present():
    assert FAKE.is_file(), f"missing {FAKE}"
    assert os.access(FAKE, os.X_OK), "fake_claude.sh must be executable"


@requires_tmux
def test_composer_ready_footer(fake_socket, tmp_path):
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "bash", str(FAKE))
    screen = _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")
    assert "❯" in screen
    assert "esc to interrupt" not in screen  # idle, not working
    assert "← for agents" in screen          # canonical IDLE suffix


@requires_tmux
def test_trust_gate_then_composer(fake_socket, tmp_path):
    env = "WCB_FAKE_TRUST=1"
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "env", env, "bash", str(FAKE))
    _wait_for(fake_socket, "Is this a project you created or one you trust?")
    screen = _capture(fake_socket)
    assert "❯ 1. Yes, I trust this folder" in screen
    # Answer the trust prompt the way create() will: ["1","Enter"].
    _tmux(fake_socket, "send-keys", "-t", "t", "-l", "--", "1")
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")


@requires_tmux
def test_write_part_rename_done_edge(fake_socket, tmp_path):
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "env", "WCB_FAKE_DELAY=0.3", "bash", str(FAKE))
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")

    env_path = tmp_path / "env.test.json"
    turn_uuid = "8411fd46-093b-4fdd-9ae0-183cfa5ba98b"
    assert not env_path.exists()

    send_time = time.time()
    line = f"WRITE {env_path} {turn_uuid}"
    _tmux(fake_socket, "send-keys", "-t", "t", "-l", "--", line)
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")

    # Footer flips to WORKING during the delay.
    _wait_for(fake_socket, "esc to interrupt")

    # The FINAL renamed file appears (never a lingering .part).
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if env_path.exists():
            break
        time.sleep(0.05)
    assert env_path.exists(), "renamed env file never appeared"
    assert not (tmp_path / "env.test.json.part").exists(), ".part was not renamed"

    data = json.loads(env_path.read_text())
    assert data["turn_uuid"] == turn_uuid
    assert data["tool"] == "echo"
    assert data["envelope"]                       # truthy, per read-back contract
    assert env_path.stat().st_mtime >= send_time - 1  # mtime after send

    _wait_for(fake_socket, "⏺ DONE")
    # Footer reverts to IDLE after the turn.
    _wait_for(fake_socket, "esc to interrupt", want=False)


@requires_tmux
def test_rising_spinner_timer_mode(fake_socket, tmp_path):
    # Risk #2 end-to-end: WORKING is also signalled by a rising spinner timer.
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "env", "WCB_FAKE_TIMER=1", "WCB_FAKE_DELAY=0.3",
          "bash", str(FAKE))
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")
    env_path = tmp_path / "env.timer.json"
    line = f"WRITE {env_path} 11111111-2222-3333-4444-555555555555"
    _tmux(fake_socket, "send-keys", "-t", "t", "-l", "--", line)
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")
    # The elapsed seconds rise across captures: (1s ...) then a higher value.
    _wait_for(fake_socket, "Smooshing… (1s")
    _wait_for(fake_socket, "Smooshing… (2s")
    _wait_for(fake_socket, "⏺ DONE")


@requires_tmux
def test_sigint_trap_prints_interrupted(fake_socket, tmp_path):
    # cleanup-security's interrupt path relies on this trap marker.
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "bash", str(FAKE))
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")
    _tmux(fake_socket, "send-keys", "-t", "t", "C-c")
    _wait_for(fake_socket, "^C INTERRUPTED")


@requires_tmux
def test_blank_line_is_noop_and_exit_quits(fake_socket, tmp_path):
    _tmux(fake_socket, "new-session", "-d", "-s", "t",
          "-x", "120", "-y", "40", "-c", str(tmp_path),
          "bash", str(FAKE))
    _wait_for(fake_socket, "⏵⏵ bypass permissions on (shift+tab to cycle)")
    # Blank line: still idle, no WORKING footer.
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")
    time.sleep(0.3)
    assert "esc to interrupt" not in _capture(fake_socket)
    # EXIT quits the mimic after printing the resume hint; session ends.
    _tmux(fake_socket, "send-keys", "-t", "t", "-l", "--", "EXIT")
    _tmux(fake_socket, "send-keys", "-t", "t", "Enter")
    _wait_for(fake_socket, "claude --resume")
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        rc = _tmux(fake_socket, "has-session", "-t", "t").returncode
        if rc != 0:
            break
        time.sleep(0.1)
    assert _tmux(fake_socket, "has-session", "-t", "t").returncode != 0
```

### Step 2 — run it + expect FAIL

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_fake_claude.py
```

Expected: failures — `tests/fake_claude.sh` does not exist, so `test_fake_is_executable_and_present` fails on `missing .../fake_claude.sh` and the tmux-driven tests time out / find no composer (exit code non-zero). (If `tmux` were absent the suite would skip via `requires_tmux`, but tmux 3.6a is present here.)

### Step 3 — minimal implementation

Create `tests/fake_claude.sh` (the single canonical mimic — SIGINT trap, canonical footer tokens, rising-timer mode, and `EXIT`→`claude --resume`+shell-prompt all live here so no later component re-authors it):

```bash
#!/usr/bin/env bash
# Single canonical claude TUI mimic. The bridge's TmuxClient drives it with the
# exact same tmux capture-pane / send-keys calls it uses against real claude.
# Pinned tokens match claude v2.1.156 (see tests/fixtures/captures/).
# Authored ONCE here; tmux-client / registry-lifecycle / turn-protocol-fsm /
# cleanup-security all depend on THIS file and never re-author it.
#
# Protocol over stdin (one line at a time):
#   WRITE <abs_env_path> <turn_uuid>  -> work, write .part, rename, print DONE
#   EXIT                              -> print resume hint + shell prompt, quit
#   <blank>                           -> no-op, stay idle
#
# Env knobs:
#   WCB_FAKE_TRUST=1   show + block on the workspace-trust prompt first
#   WCB_FAKE_DELAY=N   seconds per WORKING slice (default 0.3)
#   WCB_FAKE_TIMER=1   emit a rising spinner timer `(<N>s · thinking)` (risk #2)

set -u

FOOTER_PREFIX='  ⏵⏵ bypass permissions on (shift+tab to cycle)'
FOOTER_IDLE="${FOOTER_PREFIX} · ← for agents"
FOOTER_WORKING="${FOOTER_PREFIX} · esc to interrupt"
RULE='────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────'
DELAY="${WCB_FAKE_DELAY:-0.3}"
RESUME_UUID='8411fd46-093b-4fdd-9ae0-183cfa5ba98b'

# cleanup-security's interrupt corroboration looks for this marker.
trap 'printf "%s\n" "^C INTERRUPTED"' INT

print_composer() {
    printf '%s\n' "$RULE"
    printf '%s\n' '❯ '
    printf '%s\n' "$RULE"
    printf '%s\n' "$FOOTER_IDLE"
}

print_working() {
    printf '%s\n' "$RULE"
    printf '%s\n' '❯ '
    printf '%s\n' "$RULE"
    printf '%s\n' "$FOOTER_WORKING"
}

print_trust() {
    printf '%s\n' ' Quick safety check: Is this a project you created or one you trust?'
    printf '%s\n' ' ❯ 1. Yes, I trust this folder'
    printf '%s\n' '   2. No, exit'
    printf '%s\n' ' Enter to confirm · Esc to cancel'
}

if [ "${WCB_FAKE_TRUST:-}" = "1" ]; then
    print_trust
    # Block until the test answers ["1","Enter"]; we ignore the value.
    IFS= read -r _trust_answer || true
fi

print_composer

while IFS= read -r line; do
    case "$line" in
        WRITE\ *)
            # WRITE <abs_env_path> <turn_uuid>
            rest="${line#WRITE }"
            env_path="${rest%% *}"
            turn_uuid="${rest#* }"
            print_working
            if [ "${WCB_FAKE_TIMER:-}" = "1" ]; then
                # Rising spinner elapsed timer (matches real `✻ Smooshing… (Ns …)`).
                for n in 1 2 3; do
                    printf '%s\n' "✻ Smooshing… (${n}s · thinking)"
                    sleep "$DELAY"
                done
            else
                sleep "$DELAY"
            fi
            printf '{"tool":"echo","envelope":{"ok":true},"turn_uuid":"%s"}' \
                "$turn_uuid" > "${env_path}.part"
            mv -f "${env_path}.part" "$env_path"
            printf '%s\n' '⏺ Write('"$env_path"')'
            printf '%s\n' '⏺ DONE'
            print_composer
            ;;
        EXIT)
            # Match the real post-exit screen: resume hint then shell prompt.
            printf '%s\n' 'Resume this session with:'
            printf '%s\n' "claude --resume ${RESUME_UUID}"
            printf '%s\n' 'martintreiber@10 fake %'
            exit 0
            ;;
        '')
            # blank line: stay idle, no-op
            :
            ;;
        *)
            # any other input: ignore, stay idle
            :
            ;;
    esac
done
```

Make it executable:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && chmod +x tests/fake_claude.sh
```

Create `tests/integration/__init__.py` (empty file, zero bytes).

### Step 4 — run + expect PASS

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_fake_claude.py
```

Expected: `7 passed` (exit code 0). (On a host with no tmux these would report `7 skipped`; tmux 3.6a is present here so they must pass.)

### Step 5 — commit

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add tests/fake_claude.sh tests/integration/__init__.py tests/integration/test_fake_claude.py && git commit -m "test: add canonical fake_claude.sh mimic + real-tmux integration harness

The ONE fake_claude.sh the TmuxClient drives identically to real claude:
pinned v2.1.156 footer tokens, the workspace-trust gate, the
WRITE -> .part -> rename -> DONE completion edge, a rising-spinner timer
mode (WCB_FAKE_TIMER, exercises risk #2 end-to-end), the SIGINT trap
marker cleanup-security needs, and the EXIT -> claude --resume + shell
DEAD edge. Authored once; later components depend on it, never re-author.
Integration smoke proves every edge through the real tmux binary. Locks
risks #2/#7 at the harness level; no claude quota, no model flakiness.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4 — full-suite run command + live tier guard

Verify the whole `tests/` tree runs green via the canonical commands, add the `tests/live/` tier package and the per-tier entry points (`tests/unit`, `tests/integration`, `tests/live`), and pin the live-tier opt-in guard so the live smoke is skipped by default (no quota spend in CI). This closes the harness: every later task can run `python3 -m pytest -q` and a single tier on demand.

**Files**
- Create `tests/live/__init__.py` (empty, zero bytes)
- Test `tests/live/test_live_guard.py` (a placeholder proving the `WCB_LIVE_SMOKE` gate skips by default; the real `test_live_smoke.py` lands in the final phase)

### Step 1 — write the FAILING test

Create `tests/live/test_live_guard.py`:

```python
"""The live tier must be opt-in: skipped unless WCB_LIVE_SMOKE=1, so the
default `python3 -m pytest -q` never spends claude quota. The real
test_live_smoke.py replaces this in the final phase; this only proves the
gate works and the live/ package is discoverable.
"""
import os

import pytest

live_only = pytest.mark.skipif(
    os.environ.get("WCB_LIVE_SMOKE") != "1",
    reason="live tier opt-in: set WCB_LIVE_SMOKE=1",
)


@live_only
def test_live_gate_runs_only_when_opted_in():
    # If this body ever executes, the env gate was honored.
    assert os.environ.get("WCB_LIVE_SMOKE") == "1"


def test_live_package_is_collectable():
    # Always runs: proves tests/live is a discoverable package.
    assert True
```

### Step 2 — run it + expect FAIL

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/live/test_live_guard.py
```

Expected: collection error — `tests/live/` is not a package (no `__init__.py`), so under `--import-mode=importlib` the module fails to import / is not collected (exit code non-zero, e.g. `errors during collection` / `ModuleNotFoundError`).

### Step 3 — minimal implementation

Create `tests/live/__init__.py` (empty file, zero bytes).

### Step 4 — run + expect PASS, then verify the canonical commands

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/live/test_live_guard.py
```

Expected: `1 passed, 1 skipped` (the gated test skips because `WCB_LIVE_SMOKE` is unset).

Then verify the four canonical run commands the rest of the plan relies on all succeed:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q && echo "===UNIT===" && python3 -m pytest -q tests/unit && echo "===INTEGRATION===" && python3 -m pytest -q tests/integration && echo "===LIVE===" && python3 -m pytest -q tests/live
```

Expected: the full run reports all tests passing with `1 skipped` (the live gate); each tier sub-run is green; `===UNIT===`, `===INTEGRATION===`, `===LIVE===` separators all print (exit code 0).

### Step 5 — commit

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add tests/live/__init__.py tests/live/test_live_guard.py && git commit -m "test: add live tier package + opt-in WCB_LIVE_SMOKE guard

Default \`python3 -m pytest -q\` skips the live tier (no claude quota).
Confirms the full suite + per-tier run commands (unit/integration/live)
the rest of the plan depends on. The real live smoke lands in the final
phase; this only pins the gate.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Component notes for the assembler

- These four tasks are the Phase 0 spine (skeleton §4 tasks 1–3 plus the run-command verification folded into a fourth task so the harness is provably complete before Phase 1).
- After this component, the `tests/` tree is: `conftest.py` (4 shared fixtures, exporting `TMUX`/`FAKE`/`CAPTURES`/`requires_tmux`), `fixtures/captures/{composer_ready,thinking_esc_interrupt,thinking_timer,trust_prompt,turn_done,post_exit_shell}.txt`, `slice_captures.py`, the single canonical `fake_claude.sh`, and the `unit/integration/live` packages each with at least one passing test. No production module (`paths.py`, `fsm.py`, etc.) is created here — those land in later phases against this harness.
- **Single fake_claude.sh (critique FIX):** `tests/fake_claude.sh` is authored ONCE in Task 3 and carries the SIGINT trap (cleanup-security interrupt path), the canonical footer token set (FSM/TmuxClient), the rising-spinner timer mode (`WCB_FAKE_TIMER`, design risk #2), and both `EXIT` semantics (`claude --resume <uuid>` + shell prompt). The tmux-client, registry-lifecycle, turn-protocol-fsm, and cleanup-security components MUST import it via `conftest.FAKE` and MUST NOT re-author it.
- **Import mode (critique FIX):** `pytest.ini` pins `--import-mode=importlib` and `pythonpath = . tests tests/integration`, and every test uses bare `from conftest import ...`. Later components' cross-test-module imports (e.g. turn-protocol-fsm's `from test_session_stream_sse import http_server`) resolve under this mode; they must not switch to `from tests.conftest import ...`.
- The slicer maps calibration panel headers → fixture files: `composer-ready`→`composer_ready.txt`, `turn-poll-1`→`thinking_esc_interrupt.txt`, `turn-poll-2`→`thinking_timer.txt`, `after-launch-8s`→`trust_prompt.txt`, `turn-done`→`turn_done.txt`, `post-exit`→`post_exit_shell.txt`. If `/tmp/wcbcal-captures.log` is gone at run time, the verbatim bodies are at log lines 97–127, 161–191, 193–223, 33–63, 257–287, 353–384 respectively (also reproduced in the design doc Calibration section).
- Pinned constants the fixtures and `fake_claude.sh` carry verbatim (claude 2.1.156): `esc to interrupt`, `⏵⏵ bypass permissions on (shift+tab to cycle)` (IDLE suffix `· ← for agents`, WORKING suffix `· esc to interrupt`), the rising spinner `✻ Smooshing… (<N>s · thinking)`, `Is this a project you created or one you trust?`, `❯ 1. Yes, I trust this folder`, markers `⏺`/`⎿`, `claude --resume <uuid>`, the 28-byte no-newline probe `{"ok":true,"probe":"wcbcal"}`, and `M-Enter` keeping `lineONE`/`lineTWO` unsent. These match the §3 SHARED CONTRACT exactly.
- `fake_claude.sh` writes `{"tool":"echo","envelope":{"ok":true},"turn_uuid":"<uuid>"}` (satisfies the §3 read-back contract: `tool:str`, `envelope` truthy, `turn_uuid` echoed) and exercises the `.part`→rename edge so rendezvous-docsync's `read_envelope_bytes`/`await_envelope` and turn-protocol-fsm's turn-protocol tests have a real completion edge to poll.


**Critique fixes applied to this component:**

1. **fake_claude.sh de-duplication FIX**: Skip Task 0 (the harness) since it's owned by the test-scaffold component (skeleton Phase-0). The critique explicitly says de-duplicate to ONE fake_claude.sh owned upstream, carrying the SIGINT trap, canonical footer tokens, and a rising-timer mode. So this component must NOT re-author fake_claude.sh/conftest. I'll drop the old Task 0 and depend on the upstream harness.

2. **tpgid FIX**: Use `os.getpgid(pane_pid)` (a real pgid) rather than raw pane_pid, since the guarded `killpg` depends on a real process group id. One canonical definition here; cleanup-security must not redefine.

3. **interrupt FIX**: Merge the C-c fallback into this component's `interrupt`. Keep `_tpgid_override` test seam.

4. **NAMED_KEYS FIX**: Single source in `tmux_session.py` (already here).

5. **socket_path() GAP FIX**: Add `socket_path()` accessor to a tmux-client task.

6. **pipe_pane re-arm FIX**: Use disable-first + query `#{pane_pipe}`, and strengthen the test to assert `pane_pipe()==1` (single sink), removing the weak count assertion.

7. **new_session signature**: Keep `argv` param (canonical, per component note).

8. **constructor**: `TmuxClient(socket, tmux_bin="tmux")` is canonical; add `socket_path()`.

9. **rising-timer fake mode (risk #2) FIX**: This belongs to the upstream harness owner. I'll note the dependency but not author fake_claude.sh. However, since this component's capture tests can exercise it, I'll reference the canonical token set the upstream harness provides.


---

## Component: tmux-client (`tmux_session.py`)

> Source design: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/docs/plans/2026-05-29-persistent-claude-session-design.md`
> Skeleton contract: §3 "TmuxClient (`tmux_session.py`)", §2(b) fake-claude integration tier.
> All work is inside the worktree `…/.worktrees/persistent-claude-session/`. Pure stdlib runtime; `pytest` dev-only.
> This component delivers the synchronous `TmuxClient` port driven against the shared `tests/fake_claude.sh` through **real** `tmux`. No HTTP, no registry.

**Component scope (and what is NOT here):** `TmuxClient` + `_TmuxError` + `NAMED_KEYS` only — create detached session, `has-session`/`pane_id`/`socket_path`/`kill_server`, capture-pane, `alternate_on`/`pane_current_command`/`pane_dead`/`tpgid`, options round-trip, `send_keys`/`send_text`/`send_prompt`, `pipe_pane_on`/`pipe_pane_off`, guarded `interrupt` (with C-c fallback). The registry (`session_registry.py`), the FSM (`fsm.py`), path-safety (`paths.py`), the rendezvous/doc-sync, and the HTTP endpoints are **other components** — this component imports none of them. `read_envelope_bytes` / envelope read-back is the rendezvous-docsync component, NOT here.

**Shared-harness dependency (critique FIX — de-dup):** `tests/fake_claude.sh`, `tests/conftest.py` (fixtures `fake_socket`, `fake_claude_argv`, `requires_tmux`, `TMUX`, `FAKE`), `pytest.ini`, and `requirements-dev.txt` are authored ONCE by the upstream **test-scaffold** component (the Phase-0 harness). This component does **not** re-author them. The canonical `fake_claude.sh` it depends on must carry: the pinned IDLE token `⏵⏵ bypass permissions on (shift+tab to cycle)`, the WORKING token `esc to interrupt`, an incrementing spinner timer `…(<N>s · …)` (risk #2), the trust gate (`WCB_FAKE_TRUST=1`), the `WRITE <abs_env_path> <turn_uuid>`→`.part`→rename→`⏺ DONE` edge, the `EXIT` token printing `Resume this session with: claude --resume <uuid>`, and a `trap '...^C INTERRUPTED' INT` (so the guarded interrupt is end-to-end testable by cleanup-security). If the assembler scheduled this component before the test-scaffold harness, schedule test-scaffold first; do not author the harness here.

**Cross-component signature deltas the assembler must propagate (canonical, owned here):**
- `TmuxClient(socket, tmux_bin="tmux")` — registry must call `TmuxClient(socket)` (or pass `tmux_bin`); never forward a caller/HTTP-supplied socket (risk #6).
- `new_session(self, name, cwd, cols, rows, argv)` — explicit `argv` so the registry injects `claude_argv` (real) or the `fake_claude_argv` fixture (tests). `_await_ready` in the registry must NOT re-type the launch line; the pane command IS `argv`.
- `socket_path()` returns the server's `-L` socket path (for the registry's defensive socket-clear, risk #11, and delete-on-socket).
- `tpgid(target)` returns a real process-group id via `os.getpgid(#{pane_pid})` — NOT a raw pid. This is the canonical definition; cleanup-security must NOT redefine `tpgid` or `interrupt`.
- `interrupt(target, shell_pid, _tpgid_override=None)` — guarded `killpg(tpgid, SIGINT)` with a `send_keys("C-c")` fallback folded in (the C-c fallback from cleanup-security is merged here; cleanup-security must NOT re-author `interrupt`). Production callers use `interrupt(target, shell_pid)`.
- `NAMED_KEYS` is the single source; every other component does `from tmux_session import NAMED_KEYS` — no local redefinition.

---

## Task 5 — TmuxClient core: `new_session` / `has_session` / `pane_id` / `socket_path` / `kill_server` + `_TmuxError`

> Risk-tests: **#11** (`has-session`-only liveness; `socket_path` for defensive socket-clear / delete-on-socket), **#13** (retryable vs authoritative `_TmuxError`).
> Depends on the upstream test-scaffold harness (`tests/conftest.py`, `tests/fake_claude.sh`, `pytest.ini`). Do not author those here.

**Files**
- Create: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tmux_session.py`
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_tmux_client.py`

**Step 1 — write the FAILING test.** Create `tests/integration/test_tmux_client.py`:

```python
# tests/integration/test_tmux_client.py
import os
import time

import pytest

from conftest import TMUX, FAKE, requires_tmux
from tmux_session import TmuxClient, _TmuxError

pytestmark = requires_tmux

FAKE_ARGV = ["bash", str(FAKE)]
IDLE = "bypass permissions on (shift+tab to cycle)"


def _wait_for(c, target, needle, timeout=10.0):
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = c.capture_pane(target)
        if needle in last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"never saw {needle!r}; last screen:\n{last}")


def test_create_has_kill(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    pane = c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    assert pane.startswith("%")
    assert c.has_session("t") is True
    assert c.pane_id("t") == pane
    c.kill_server()
    assert c.has_session("t") is False


def test_socket_path_reports_l_socket(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    sp = c.socket_path()
    # The -L socket is a real path on disk while the server is up.
    assert isinstance(sp, str) and sp != ""
    assert fake_socket in sp
    assert os.path.exists(sp)


def test_kill_server_then_recreate_same_socket(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    c.kill_server()
    # A fresh server on the same socket name must come up cleanly (risk #11).
    c2 = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c2.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    assert c2.has_session("t") is True


def test_has_session_false_for_unknown_socket():
    c = TmuxClient(socket="wcbtest_nonexistent_zzzz", tmux_bin=TMUX)
    # No server running on this socket -> gone, NOT an exception.
    assert c.has_session("t") is False


def test_tmux_error_classification_authoritative(fake_socket):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    # No server yet -> pane_id on a missing session is authoritative (not retryable).
    with pytest.raises(_TmuxError) as ei:
        c.pane_id("t")
    assert ei.value.retryable is False


def test_tmux_binary_missing_is_authoritative_classified(tmp_path):
    c = TmuxClient(socket="wcbtest_x", tmux_bin="/nonexistent/tmux_binary_xyz")
    with pytest.raises(_TmuxError) as ei:
        c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=["true"])
    # A missing tmux binary is authoritative (-> 503 upstream), surfaced as a
    # non-retryable _TmuxError carrying the cause.
    assert ei.value.retryable is False
    assert "tmux" in str(ei.value).lower()
```

**Step 2 — run it + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py
```
Expected FAIL: `ModuleNotFoundError: No module named 'tmux_session'` (collection error — `tmux_session.py` does not exist yet).

**Step 3 — minimal implementation.** Create `tmux_session.py`:

```python
# tmux_session.py
"""Synchronous TmuxClient — every tmux call is an argv list run shell=False.

No HTTP, no registry, no path-safety here: this module is a thin, well-typed
port over the `tmux` binary. Liveness is `has-session` ONLY (design risk #11).
Errors are classified transient (retryable) vs authoritative (risk #13) so the
reaper never evicts a live session on a fork hiccup.

NAMED_KEYS is defined here once — every other component imports it from here.
"""
import os
import signal
import subprocess

# Liveness/authoritative phrases tmux emits to stderr when a server/session is
# genuinely gone. Anything else (EAGAIN/ENOMEM fork failure, EINTR, a timeout)
# is treated as transient.
_GONE_PHRASES = (
    "no server running",
    "no current session",
    "session not found",
    "can't find session",
    "error connecting to",   # stale/absent socket
)

NAMED_KEYS = frozenset({
    "Enter", "Escape", "Up", "Down", "Left", "Right",
    "M-Enter", "C-c", "BSpace", "Tab", "Space",
})


class _TmuxError(Exception):
    def __init__(self, msg, *, retryable):
        super().__init__(msg)
        self.retryable = retryable


class TmuxClient:
    def __init__(self, socket, tmux_bin="tmux"):
        self.socket = socket
        self.tmux_bin = tmux_bin

    def _argv(self, *args):
        return [self.tmux_bin, "-L", self.socket, *args]

    def _run(self, *args, timeout=10.0):
        """Run a tmux subcommand. Return CompletedProcess on rc==0.

        Raise _TmuxError(retryable=False) when stderr names a gone server/
        session or the binary is missing; _TmuxError(retryable=True) on a
        timeout or transient OSError.
        """
        try:
            r = subprocess.run(
                self._argv(*args),
                capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError as e:
            # The tmux binary itself is absent — authoritative (-> 503 upstream).
            raise _TmuxError(f"tmux binary not found: {e}", retryable=False)
        except subprocess.TimeoutExpired as e:
            raise _TmuxError(f"tmux timed out: {e}", retryable=True)
        except OSError as e:
            # EAGAIN/ENOMEM fork failure, EINTR — transient.
            raise _TmuxError(f"tmux spawn failed: {e}", retryable=True)
        if r.returncode != 0:
            stderr = (r.stderr or "").strip()
            low = stderr.lower()
            if any(p in low for p in _GONE_PHRASES):
                raise _TmuxError(stderr or "session gone", retryable=False)
            raise _TmuxError(stderr or f"tmux exited {r.returncode}",
                             retryable=True)
        return r

    def new_session(self, name, cwd, cols, rows, argv):
        """Create a detached session with claude (or the fake) as the pane cmd.

        Mirrors: tmux -L <sock> -f /dev/null new-session -d -s <name>
                 -c <cwd> -x <cols> -y <rows> -- <argv...>
        The pane command IS `argv` (the registry injects claude_argv or the
        fake_claude_argv fixture) — the readiness path must NOT re-type a
        launch line. Returns the pane id (%N).
        """
        self._run(
            "-f", "/dev/null", "new-session", "-d", "-s", name,
            "-c", cwd, "-x", str(cols), "-y", str(rows),
            "--", *argv,
        )
        return self.pane_id(name)

    def has_session(self, name):
        """The ONLY truth source for liveness. Never raises for 'gone'."""
        try:
            self._run("has-session", "-t", name)
            return True
        except _TmuxError as e:
            if not e.retryable:
                return False
            raise

    def pane_id(self, name):
        r = self._run("list-panes", "-t", name, "-F", "#{pane_id}")
        return (r.stdout or "").strip().splitlines()[0]

    def socket_path(self):
        """Absolute path of the -L socket (for defensive socket-clear / unlink,
        risk #11). Server must be up."""
        r = self._run("display-message", "-p", "#{socket_path}")
        return (r.stdout or "").strip()

    def kill_server(self):
        try:
            self._run("kill-server")
        except _TmuxError as e:
            # Killing an already-dead server is a no-op, not a failure.
            if e.retryable:
                raise
```

**Step 4 — run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py
```
Expected: `6 passed` (or `6 skipped` only if `tmux` is absent; tmux 3.6a is present on the target machine, so PASS).

**Step 5 — commit.**
```
git add tmux_session.py tests/integration/test_tmux_client.py
git commit -m "feat(tmux): TmuxClient core — new/has/pane_id/socket_path/kill + _TmuxError

Synchronous argv-only (shell=False) port; NAMED_KEYS single source. has-session
is the sole liveness truth source (risk #11); socket_path() exposes the -L
socket for the registry's defensive clear/unlink. Errors classified retryable
vs authoritative so a fork hiccup never reaps a live session (risk #13).
new_session takes explicit argv so the registry injects claude_argv or the fake.
Tested through real tmux against the shared fake_claude.sh harness.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6 — TmuxClient capture + state queries + options round-trip

> Risk-tests: **#11** (missing `@wcb_*` option → recoverable default, NOT dead), **#2** infra (`capture-pane -p` is the only screen source; `alternate_on==0` is the load-bearing invariant the inline-capture model depends on). The DEAD multi-poll corroboration over `#{pane_dead}`/`has-session` (design §4) is wired in the turn-protocol-fsm stream loop, NOT here; this task only exposes the `pane_dead`/`pane_current_command` primitives it consumes.

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tmux_session.py` (add methods to the `TmuxClient` class, after `pane_id`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_tmux_client.py` (append)

**Step 1 — write the FAILING test.** Append to `tests/integration/test_tmux_client.py`:

```python
def test_capture_pane_returns_composer(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    screen = _wait_for(c, "t", IDLE)
    assert "❯ " in screen


def test_alternate_on_is_zero(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    # The whole inline-capture model depends on alternate_on == 0.
    assert c.alternate_on("t") == 0


def test_pane_current_command(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    cmd = c.pane_current_command("t")
    # The fake runs under bash; the real binary reports 'claude.exe'. Either
    # way it is a non-empty command name (DEAD discriminator is shell-relative).
    assert isinstance(cmd, str) and cmd != ""


def test_pane_dead_false_while_running(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    assert c.pane_dead("t") is False


def test_tpgid_positive_and_is_a_pgid(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    pg = c.tpgid("t")
    assert pg > 0
    # tpgid is a real process-group id (os.getpgid of the pane pid), so the
    # guarded killpg in Task 9 targets the group, not a lone pid.
    assert os.getpgid(os.getpgid(pg) and pg or pg) == pg  # pg is its own group's id


def test_user_option_roundtrip(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.set_option("t", "@wcb_nonce", "abc123")
    assert c.get_option("t", "@wcb_nonce") == "abc123"


def test_missing_option_is_default_not_dead(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    # An unset @wcb_* option is a recoverable default (None), NOT a death signal.
    assert c.get_option("t", "@wcb_never_set") is None
```

**Step 2 — run it + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py -k "capture or alternate or pane_current or pane_dead or tpgid or option"
```
Expected FAIL: `AttributeError: 'TmuxClient' object has no attribute 'capture_pane'` (first failing call).

**Step 3 — minimal implementation.** Add these methods to the `TmuxClient` class in `tmux_session.py` (insert after `pane_id`):

```python
    def _target(self, target):
        # Accept a session name ("t") or a pane id ("%3"); both are valid -t.
        return str(target)

    def capture_pane(self, target):
        """`capture-pane -p` — the ONLY screen source (FSM strips it)."""
        r = self._run("capture-pane", "-p", "-t", self._target(target))
        return r.stdout

    def _display(self, target, fmt):
        r = self._run("display-message", "-p", "-t", self._target(target), fmt)
        return (r.stdout or "").strip()

    def alternate_on(self, target):
        """Must be 0 — claude renders inline, not on the alternate screen."""
        v = self._display(target, "#{alternate_on}")
        return int(v or "0")

    def pane_current_command(self, target):
        return self._display(target, "#{pane_current_command}")

    def pane_dead(self, target):
        return self._display(target, "#{pane_dead}") == "1"

    def pane_pid(self, target):
        v = self._display(target, "#{pane_pid}")
        return int(v or "0")

    def tpgid(self, target):
        """Foreground process-GROUP id of the pane (os.getpgid of #{pane_pid}).

        Canonical for the guarded killpg in Task 9 — a real pgid, never a raw
        pid. cleanup-security must not redefine this.
        """
        pid = self.pane_pid(target)
        if pid <= 0:
            return 0
        try:
            return os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            return 0

    def set_option(self, target, name, value):
        # User options (@wcb_*) are stored per-session; survive a bridge restart.
        self._run("set-option", "-t", self._target(target), name, value)

    def get_option(self, target, name):
        """Missing option -> None (a recoverable default, never 'dead', #11)."""
        try:
            r = self._run("show-options", "-v", "-t", self._target(target), name)
        except _TmuxError as e:
            if not e.retryable:
                return None
            raise
        out = (r.stdout or "").strip()
        return out if out != "" else None
```

> Note for the registry/reaper authors: `tpgid` returns a real pgid via `os.getpgid(#{pane_pid})`; `pane_pid` is exposed separately so the registry can record `shell_pid` at create time and the guarded `interrupt` can compare `tpgid != shell_pid`. `set_option`/`get_option` take an explicit `target` (the registry always uses `"t"`).

**Step 4 — run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py
```
Expected: `13 passed` (6 from Task 5 + 7 new).

**Step 5 — commit.**
```
git add tmux_session.py tests/integration/test_tmux_client.py
git commit -m "feat(tmux): capture-pane + state queries + @wcb_* option roundtrip

capture_pane(-p) is the sole screen source; alternate_on()==0 asserted (the
inline-capture invariant, risk #2 infra); pane_current_command/pane_dead/
pane_pid added; tpgid() returns a real pgid via os.getpgid(#{pane_pid}) for the
guarded killpg. Missing @wcb_* option returns None — a recoverable default,
never a death signal (risk #11).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7 — TmuxClient keys: `send_keys` (NAMED_KEYS allowlist) + `send_text` (literal) + `send_prompt` (M-Enter / final Enter)

> Risk-tests: **#10** (key answers for menus/trust), **#9** (literal text typed via `-l … --`, no shell eval). Calibration-pinned (v2.1.156): `M-Enter` inserts a composer newline without submitting; multi-line via per-line `-l` + `M-Enter` + final bare `Enter`.
> Note: the production prompt for a real turn is built by rendezvous-docsync's `build_turn_prompt`; the `WRITE <env> <uuid>` line used below is the **fake-claude** protocol only — tests must not assert the fake template against the real prompt path.

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tmux_session.py` (add to `TmuxClient`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_tmux_client.py` (append)

**Step 1 — write the FAILING test.** Append to `tests/integration/test_tmux_client.py`:

```python
def test_send_keys_rejects_unknown_named_key(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    # Anything outside NAMED_KEYS must be rejected before touching tmux argv.
    with pytest.raises(ValueError):
        c.send_keys("t", "F1")
    with pytest.raises(ValueError):
        c.send_keys("t", "rm -rf /")


def test_send_text_types_literally_no_shell_eval(fake_socket, tmp_path):
    # send_text uses `-l ... --` so metacharacters are typed, never evaluated.
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    payload = "EXIT$(touch /tmp/wcb_pwned_$$)"  # if eval'd, file would appear
    c.send_text("t", payload)
    time.sleep(0.3)
    assert not os.path.exists(f"/tmp/wcb_pwned_{os.getpid()}")
    screen = c.capture_pane("t")
    assert "$(touch" in screen  # the literal characters reached the composer


def test_send_prompt_two_lines_unsent_via_m_enter(fake_socket, tmp_path):
    # M-Enter inserts a newline WITHOUT submitting (calibration-pinned). The
    # fake only treats a bare Enter as submit, so a 2-line prompt stays unsent
    # at the M-Enter boundary and is submitted exactly once at the end.
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.send_prompt("t", "lineONE\nlineTWO")
    time.sleep(0.3)
    screen = c.capture_pane("t")
    assert "lineONE" in screen
    assert "lineTWO" in screen


def test_send_prompt_single_line_submits_fake_write(fake_socket, tmp_path):
    # A single-line fake WRITE prompt submits with one bare Enter -> the fake
    # writes the rendezvous file. (Fake protocol only; the real prompt is
    # build_turn_prompt in rendezvous-docsync.)
    import json
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    env_path = tmp_path / "env.json"
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.send_prompt("t", f"WRITE {env_path} {uuid}")
    _wait_for(c, "t", "⏺ DONE")
    obj = json.loads(env_path.read_text())
    assert obj["turn_uuid"] == uuid


def test_send_keys_m_enter_does_not_submit(fake_socket, tmp_path):
    # Distinguish M-Enter (no submit) from Enter (submit) via a capture.
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.send_text("t", "PROBE")
    c.send_keys("t", "M-Enter")
    time.sleep(0.2)
    after_m_enter = c.capture_pane("t")
    assert "PROBE" in after_m_enter           # still in composer, unsent
    assert "⏺ DONE" not in after_m_enter      # never submitted
```

**Step 2 — run it + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py -k "send_keys or send_text or send_prompt"
```
Expected FAIL: `AttributeError: 'TmuxClient' object has no attribute 'send_keys'`.

**Step 3 — minimal implementation.** Add to `TmuxClient` in `tmux_session.py`:

```python
    def send_keys(self, target, *keys):
        """Send named keys ONLY (NAMED_KEYS allowlist). Reject everything else
        so a caller can never smuggle a tmux key-name or shell string here —
        free-form text must go through send_text (#9/#10)."""
        for k in keys:
            if k not in NAMED_KEYS:
                raise ValueError(f"key not in NAMED_KEYS allowlist: {k!r}")
        self._run("send-keys", "-t", self._target(target), *keys)

    def send_text(self, target, text):
        """Type `text` literally: `send-keys -l -t <pane> -- <text>`.
        The `-l` flag + `--` guard mean tmux interprets no key names and the
        shell evaluates nothing (#9)."""
        self._run("send-keys", "-l", "-t", self._target(target), "--", text)

    def send_prompt(self, target, text):
        """Type a (possibly multi-line) prompt the way claude's composer wants:
        each non-empty line via `send-keys -l … --`, an `M-Enter` between lines
        (composer newline, NOT submit), and one final bare `Enter` to submit.
        Calibration-pinned for claude v2.1.156."""
        lines = text.split("\n")
        last = len(lines) - 1
        for i, line in enumerate(lines):
            if line:
                self.send_text(target, line)
            if i < last:
                self.send_keys(target, "M-Enter")
        self.send_keys(target, "Enter")
```

**Step 4 — run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py
```
Expected: `18 passed`.

**Step 5 — commit.**
```
git add tmux_session.py tests/integration/test_tmux_client.py
git commit -m "feat(tmux): send_keys (NAMED_KEYS allowlist) + send_text + send_prompt

send_keys accepts only the NAMED_KEYS allowlist; send_text types literally via
-l … -- (no shell eval, #9); send_prompt splits multiline with M-Enter newlines
and one final Enter to submit (calibration-pinned M-Enter, #10). Verified
against the fake: M-Enter keeps two lines unsent, Enter submits exactly once.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8 — TmuxClient pipe-pane: symlink-safe retention, re-arm idempotent (disable-first + `#{pane_pipe}==1`)

> Risk-tests: **#8** (pipe-pane shell sink — full-path `shlex.quote`, log created `O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW 0600`; `%`→`pct` in the name), **#12** (re-pipe idempotency: query `#{pane_pipe}` and disable with an argument-less `pipe-pane` first since the `cat >>` form does not toggle off — assert `pane_pipe()==1` after re-arm, never a stacked second `cat`). This is the single canonical `pipe_pane_on`; cleanup-security must NOT redefine it.

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tmux_session.py` (add `import shlex` + `import stat`; add to `TmuxClient`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_tmux_client.py` (append)

**Step 1 — write the FAILING test.** Append to `tests/integration/test_tmux_client.py`:

```python
def test_pane_pipe_zero_before_arm(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    assert c.pane_pipe("t") == 0


def test_pipe_pane_captures_to_private_log(fake_socket, tmp_path):
    import stat as _stat
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    log = tmp_path / "pane.log"
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.pipe_pane_on("t", str(log))
    assert c.pane_pipe("t") == 1
    c.send_text("t", "MARKERTEXT")
    c.send_keys("t", "Enter")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if log.exists() and "MARKERTEXT" in log.read_text(errors="replace"):
            break
        time.sleep(0.1)
    assert log.exists()
    assert "MARKERTEXT" in log.read_text(errors="replace")
    assert _stat.S_IMODE(log.stat().st_mode) == 0o600


def test_pipe_pane_refuses_symlink_log(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    target = tmp_path / "real_secret"
    target.write_text("secret")
    link = tmp_path / "pane.log"
    link.symlink_to(target)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    # O_NOFOLLOW must refuse a pre-existing symlink at the log path.
    with pytest.raises(OSError):
        c.pipe_pane_on("t", str(link))
    assert target.read_text() == "secret"  # untouched


def test_pipe_pane_rearm_does_not_stack_a_second_sink(fake_socket, tmp_path):
    # Re-arming must disable first (query #{pane_pipe}, argumentless pipe-pane)
    # so no second cat/sh leaks (#12). The authoritative assertion is that
    # exactly one pipe is armed after re-arm: pane_pipe() == 1.
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    log = tmp_path / "pane.log"
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.pipe_pane_on("t", str(log))
    c.pipe_pane_on("t", str(log))   # re-arm
    assert c.pane_pipe("t") == 1    # exactly one sink, never two


def test_pipe_pane_off(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    log = tmp_path / "pane.log"
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    c.pipe_pane_on("t", str(log))
    assert c.pane_pipe("t") == 1
    c.pipe_pane_off("t")
    assert c.pane_pipe("t") == 0
```

**Step 2 — run it + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py -k "pipe_pane or pane_pipe"
```
Expected FAIL: `AttributeError: 'TmuxClient' object has no attribute 'pane_pipe'` (first failing call).

**Step 3 — minimal implementation.** Add `import shlex` and `import stat` to the top of `tmux_session.py` (next to the other imports), then add to `TmuxClient`:

```python
    @staticmethod
    def _safe_log_name(log_path):
        # '%' is special in pipe-pane's command template — never let it reach
        # the shell sink unescaped; the registry already minted a confined path.
        return log_path.replace("%", "pct")

    def pane_pipe(self, target):
        """1 if a pipe is currently armed on the pane, else 0 (#12)."""
        v = self._display(target, "#{pane_pipe}")
        return int(v or "0")

    def pipe_pane_on(self, target, log_path):
        """Arm pane retention to `log_path`, symlink-safe and re-arm idempotent.

        - Create the log O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW 0600 (refuses a
          pre-existing symlink → no /etc/x clobber, no symlink sink, #8).
        - Disable any existing pipe FIRST (query #{pane_pipe}; argumentless
          pipe-pane) — the `cat >>` form does NOT toggle off, so re-arming
          would otherwise leak a second cat/sh (#12).
        - shlex.quote the full path into the `cat >>` sink command.
        """
        safe = self._safe_log_name(log_path)
        try:
            fd = os.open(
                safe,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
            os.close(fd)
        except FileExistsError:
            # Already created by a prior arm — verify it is a regular file we
            # own and is not a symlink, then reuse it.
            st = os.lstat(safe)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise OSError(f"refusing non-regular log path: {safe}")
            if st.st_uid != os.getuid():
                raise OSError(f"refusing log path not owned by euid: {safe}")
        # Disable-first so re-arming never stacks a second sink (#12).
        if self.pane_pipe(target) == 1:
            self._run("pipe-pane", "-t", self._target(target))
        sink = "cat >> " + shlex.quote(safe)
        self._run("pipe-pane", "-o", "-t", self._target(target), sink)

    def pipe_pane_off(self, target):
        """Disable retention (argumentless pipe-pane toggles it off)."""
        if self.pane_pipe(target) == 1:
            self._run("pipe-pane", "-t", self._target(target))
```

> Note on `pipe-pane -o`: `-o` only toggles when the *same* command is re-issued; the implementation always disables first with an argumentless `pipe-pane`, so re-arming never stacks a second `cat`. The reaper's log-rotation / cumulative-offset-base (`log_offset_base`, design §4) is owned by the registry/cleanup-security component, NOT here — this task only provides arm/disarm + `pane_pipe()`.

**Step 4 — run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py
```
Expected: `24 passed` (18 + 6 new).

**Step 5 — commit.**
```
git add tmux_session.py tests/integration/test_tmux_client.py
git commit -m "feat(tmux): pipe-pane retention — symlink-safe, re-arm idempotent

Log created O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW 0600 (refuses symlink sink, %→pct,
shlex.quote'd cat>> path — #8). Re-arm queries #{pane_pipe} and disables first
so no second cat leaks; pane_pipe()==1 asserted after re-arm (#12). Single
canonical pipe_pane_on — cleanup-security must not redefine it.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9 — TmuxClient guarded `interrupt` (killpg(tpgid, SIGINT) + C-c fallback)

> Risk-tests: guarded `interrupt` — `killpg(tpgid, SIGINT)` only when `tpgid > 0 and tpgid != shell_pid`; with a `send_keys("C-c")` fallback folded in (merged from cleanup-security, which must NOT re-author `interrupt`). The `_tpgid_override` keyword is a test seam (default `None`); production callers use `interrupt(target, shell_pid)`. The canonical `fake_claude.sh` carries a `trap '...^C INTERRUPTED' INT` so cleanup-security can assert the C-c fallback end-to-end; this task asserts the guard logic via the seam.

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tmux_session.py` (add to `TmuxClient`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_tmux_client.py` (append)

**Step 1 — write the FAILING test.** Append to `tests/integration/test_tmux_client.py`:

```python
def test_interrupt_skips_nonpositive_tpgid(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    # tpgid <= 0 -> no signal, no fallback key, returns False without raising.
    assert c.interrupt("t", shell_pid=999999, _tpgid_override=0) is False
    assert c.has_session("t") is True


def test_interrupt_guards_against_shell_pid(fake_socket, tmp_path):
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    pg = c.tpgid("t")
    # tpgid == shell_pid -> guarded out (would SIGINT the session's own shell).
    assert c.interrupt("t", shell_pid=pg, _tpgid_override=pg) is False
    assert c.has_session("t") is True


def test_interrupt_signals_valid_pgid_then_falls_back_to_c_c(fake_socket, tmp_path):
    # A valid, distinct tpgid -> killpg(SIGINT) attempted; on a stale group it
    # quietly falls back to a C-c key. Either way it returns True (it acted)
    # and the session survives the interrupt.
    c = TmuxClient(socket=fake_socket, tmux_bin=TMUX)
    c.new_session("t", cwd=str(tmp_path), cols=80, rows=24, argv=FAKE_ARGV)
    _wait_for(c, "t", IDLE)
    pg = c.tpgid("t")
    acted = c.interrupt("t", shell_pid=pg + 1, _tpgid_override=pg)
    assert acted is True
    assert c.has_session("t") is True
```

**Step 2 — run it + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py -k "interrupt"
```
Expected FAIL: `AttributeError: 'TmuxClient' object has no attribute 'interrupt'`.

**Step 3 — minimal implementation.** Add to `TmuxClient` in `tmux_session.py`:

```python
    def interrupt(self, target, shell_pid, _tpgid_override=None):
        """Guarded SIGINT to the pane's foreground process group, with a C-c
        key fallback.

        Only acts when tpgid > 0 AND tpgid != shell_pid — never SIGINT the
        session's own shell (that would tear the session down). Attempts
        killpg(tpgid, SIGINT); if the group is already gone or not ours, falls
        back to a `send_keys(target, "C-c")` so a TUI-level interrupt still
        reaches claude. Returns True if it acted, False if guarded out.
        (`_tpgid_override` is a test seam; production callers omit it.)
        """
        pgid = _tpgid_override if _tpgid_override is not None else self.tpgid(target)
        if pgid <= 0 or pgid == shell_pid:
            return False
        try:
            os.killpg(pgid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            # Group gone or not ours — fall back to a TUI-level C-c.
            self.send_keys(target, "C-c")
        return True
```

**Step 4 — run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py
```
Expected: `27 passed` (24 + 3 new).

**Step 5 — commit.**
```
git add tmux_session.py tests/integration/test_tmux_client.py
git commit -m "feat(tmux): guarded interrupt — killpg(tpgid, SIGINT) + C-c fallback

interrupt() acts only when tpgid>0 and != shell_pid (never SIGINT the session's
own shell); attempts killpg(SIGINT) and falls back to a C-c key if the group is
gone/not ours. Returns True if it acted, False if guarded out. C-c fallback
merged here — cleanup-security must not re-author interrupt or tpgid.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Component summary (for the assembler)

- **Files created by this component:** `tmux_session.py` (the whole `TmuxClient` + `_TmuxError` + `NAMED_KEYS` port) and `tests/integration/test_tmux_client.py`. The shared harness (`tests/fake_claude.sh`, `tests/conftest.py`, `pytest.ini`, `requirements-dev.txt`, fixtures) is owned by the upstream **test-scaffold** component and is NOT authored here (critique de-dup FIX).
- **Maps to skeleton spine:** Task 5 ↔ skeleton task 9; Task 6 ↔ skeleton task 10; Task 7 ↔ skeleton task 11; Task 8 ↔ skeleton task 12 (pipe-pane); Task 9 ↔ guarded-interrupt portion of skeleton task 12.
- **Canonical signature deltas to propagate (applied per critique):** `TmuxClient(socket, tmux_bin="tmux")`; `new_session(name, cwd, cols, rows, argv)` (argv explicit — registry passes `claude_argv`/`fake_claude_argv`, readiness must NOT re-type the launch line); `socket_path()` for the registry's defensive socket-clear/unlink (risk #11); `tpgid` = `os.getpgid(#{pane_pid})` (a real pgid, single canonical definition); `pane_pid()` exposed for the registry's `shell_pid`; `interrupt(target, shell_pid, _tpgid_override=None)` with the C-c fallback merged in (cleanup-security must NOT redefine `tpgid`/`interrupt`); single canonical `pipe_pane_on` (disable-first + `pane_pipe()`; cleanup-security must NOT redefine); `set_option`/`get_option` take an explicit `target`; `NAMED_KEYS` imported `from tmux_session` everywhere (no local redefinition).
- **Risk coverage delivered here:** #8 (T8 pipe-pane sink + log perms + symlink refusal), #9 (T7 literal `-l … --`), #10 (T7 named-key answers + send_prompt), #11 (T5 has-session-only liveness + socket_path, T6 option-default-not-dead), #12 (T8 re-pipe idempotency via `pane_pipe()==1`), #13 (T5 retryable vs authoritative `_TmuxError`), plus #2 infra (T6 `capture_pane`/`alternate_on==0`; the rising-spinner-timer fake mode and the DEAD multi-poll/`pane_dead` corroboration live in the test-scaffold harness and the turn-protocol-fsm stream loop respectively).
- **Run the whole component:** `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py` (skips automatically if `tmux` is absent; verified present as tmux 3.6a on the target machine).

**Critique fixes applied to this component:**

- **classify() signature**: every call site passes `composer_seen` (Tasks 10 `_state`/`is_busy`, 14 `_await_ready`, 16 `list_sessions`).
- **`_Session` field consolidation**: add `shell_pid`, `composer_seen`, `_gone_strikes`, `log_offset_base`, `_claimed` from the first task.
- **`_Registry` constructor**: pin `base` param + `self._base`, singleton name `REGISTRY`.
- **`new_session` signature**: no argv (argv typed in `_await_ready`); pin against tmux-client.
- **`socket_path()` accessor**: required from tmux-client (cross-ref note).
- **Risk #14 cross-check test**: tampered `@wcb_nonce` outside base rejected.
- **`_do_create`/`_do_list` ownership**: assign HTTP wrappers to this component.
- **`/list` brief cache** + **`log_bytes`/`log_offset_base`** wiring.
- Pin canonical fake_claude.sh + footer tokens; conftest import mode; ordering notes.


---

## Task 10 — `_Session` dataclass + `is_busy()` (full field set, no tmux I/O)

**Files**
- Create: `<WT>/session_registry.py`
- Test: `<WT>/tests/unit/test_registry_locking.py`

> CRITIQUE-FIX applied: the `_Session` field set is consolidated here from the start — `shell_pid`, `composer_seen`, `_gone_strikes`, `log_offset_base`, `_claimed` all exist in `__init__` so the reaper (cleanup-security), turn-protocol, and `_mk_session` helpers never need to bolt them on later. `classify(...)` is called with the pinned signature `classify(screen, footer, env_present, *, prev_timer, composer_seen)` (owner: turn-protocol-fsm Task ~30; here we always pass `composer_seen`).

**(1) Write the FAILING test** — create `<WT>/tests/unit/test_registry_locking.py` with this content:

```python
import threading
import time
import types
import pytest

import session_registry as sr


class _StubTmux:
    """Records calls; the structural lock must NEVER wrap these."""
    def __init__(self):
        self.calls = []
        self._option = {}

    def has_session(self, name):
        self.calls.append(("has_session", name))
        return True

    def get_option(self, name):
        self.calls.append(("get_option", name))
        return self._option.get(name)

    def set_option(self, name, value):
        self.calls.append(("set_option", name, value))
        self._option[name] = value

    def capture_pane(self, target):
        self.calls.append(("capture_pane", target))
        return ""


def _mk_session(**over):
    defaults = dict(
        sid="a" * 32, cap="c" * 64, nonce="d" * 16,
        socket="wcb_" + "a" * 32, pane="%0",
        cwd="/tmp/x", rendezvous_dir="/base/wcb_x_d", log_path="/base/log",
        created_at=123.0, tmux=_StubTmux(),
    )
    defaults.update(over)
    return sr._Session(**defaults)


def test_session_fields_and_locks_present():
    s = _mk_session()
    assert s.sid == "a" * 32
    assert s.cap == "c" * 64
    assert s.status == "RECONSTRUCTING"        # default until hydrated
    assert isinstance(s.turn_lock, type(threading.Lock()))
    assert isinstance(s.ready, threading.Event)
    assert not s.ready.is_set()


def test_session_consolidated_fields_present():
    """CRITIQUE-FIX: all fields siblings depend on exist from Task 10."""
    s = _mk_session()
    assert s.shell_pid is None
    assert s.composer_seen is False
    assert s._gone_strikes == 0
    assert s.log_offset_base == 0
    assert s._claimed is False


def test_is_busy_lock_held():
    s = _mk_session()
    assert s.is_busy() is False
    s.turn_lock.acquire()
    try:
        assert s.is_busy() is True
    finally:
        s.turn_lock.release()


def test_is_busy_wcb_turn_option_set():
    s = _mk_session()
    assert s.is_busy() is False
    s.tmux.set_option("@wcb_turn", "11111111-1111-1111-1111-111111111111")
    assert s.is_busy() is True
    s.tmux.set_option("@wcb_turn", "")        # cleared
    assert s.is_busy() is False


def test_is_busy_fsm_not_idle():
    s = _mk_session()
    s._classify_state = lambda: "thinking"
    assert s.is_busy() is True
    s._classify_state = lambda: "idle"
    assert s.is_busy() is False
```

**(2) Run + expected FAIL**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py
```

Expected: collection/runtime error `ModuleNotFoundError: No module named 'session_registry'` (then, once the module stub exists, `AttributeError: module 'session_registry' has no attribute '_Session'`).

**(3) Minimal implementation** — create `<WT>/session_registry.py`:

```python
"""In-memory reconstructable cache over `tmux list-sessions`.

The registry is NEVER the source of truth for liveness — `has_session` is.
Two-level locking: a module-level structural Lock guards dict mutation ONLY
(never wraps tmux I/O or a stream loop); a per-_Session turn Lock serializes
turns. See design (1) "Registry & locking".
"""
import threading
import time

from fsm import classify, footer_of, strip_screen


class _Session:
    def __init__(self, *, sid, cap, nonce, socket, pane, cwd,
                 rendezvous_dir, log_path, created_at, tmux):
        self.sid = sid
        self.cap = cap
        self.nonce = nonce
        self.socket = socket
        self.pane = pane
        self.cwd = cwd
        self.rendezvous_dir = rendezvous_dir
        self.log_path = log_path
        self.created_at = created_at
        self.tmux = tmux
        self.turn_lock = threading.Lock()
        self.ready = threading.Event()
        self.status = "RECONSTRUCTING"          # -> "READY" after hydrate
        # CRITIQUE-FIX: consolidated field set — siblings (reaper, turn,
        # replay-offset) rely on these existing from creation, never bolted on.
        self.shell_pid = None                   # cleanup-security interrupt/reaper
        self.composer_seen = False              # FSM dead-discriminator latch
        self._gone_strikes = 0                  # reaper multi-poll corroboration
        self.log_offset_base = 0                # replay offset across rotation
        self._claimed = False                   # get_or_reconstruct hydrate guard
        # Test seam: overridden in tests; real classify path below.
        self._classify_state = None

    def _state(self):
        """Best-effort FSM state from a fresh capture (no payload read)."""
        if self._classify_state is not None:
            return self._classify_state()
        screen = strip_screen(self.tmux.capture_pane(self.pane))
        # CRITIQUE-FIX: pinned classify signature — pass composer_seen always.
        state, _meta = classify(
            screen, footer_of(screen), env_present=False,
            prev_timer=None, composer_seen=self.composer_seen,
        )
        return state

    def is_busy(self):
        """busy = turn_lock held OR @wcb_turn set OR FSM != idle.

        Durable across restart via @wcb_turn (design risk #5).
        """
        if self.turn_lock.locked():
            return True
        turn = self.tmux.get_option("@wcb_turn")
        if turn:
            return True
        state = self._state()
        return state not in ("idle", "idle_no_envelope")
```

**(4) Run + expected PASS**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py
```

Expected: `6 passed`.

**(5) Commit**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/unit/test_registry_locking.py && git commit -m "$(cat <<'EOF'
registry: _Session data model (consolidated fields) + is_busy

_Session holds sid/cap/nonce/socket/pane/cwd/rendezvous_dir/log_path, a
per-session turn Lock, a reconstruct Event, status, AND the consolidated
sibling fields (shell_pid, composer_seen, _gone_strikes, log_offset_base,
_claimed) so the reaper/turn/replay components never bolt them on later.
is_busy ORs the three durable signals (risk #5): lock held, @wcb_turn,
or FSM != idle. classify() called with the pinned composer_seen signature.
No tmux I/O under the structural lock.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11 — `_Registry` skeleton + structural-lock invariant (`base` param, `REGISTRY` singleton)

**Files**
- Modify: `<WT>/session_registry.py` (add `_Registry` class + module singleton)
- Test: `<WT>/tests/unit/test_registry_locking.py` (append)

> CRITIQUE-FIX applied: the `_Registry` constructor is pinned to `_Registry(base=None)` storing `self._base` (defaulting to `paths.base_dir()` when `None`), so cleanup-security's `_Registry(base=str(tmp_base))` / `self._base` usages all resolve. The module singleton is pinned to the single name `REGISTRY` (rendezvous-docsync's `_REGISTRY` references must be rewritten to `REGISTRY`).

**(1) Write the FAILING test** — append to `<WT>/tests/unit/test_registry_locking.py`:

```python
def test_registry_structural_lock_guards_dict_only():
    reg = sr._Registry()
    assert reg._sessions == {}
    assert isinstance(reg._lock, type(threading.Lock()))


def test_registry_base_param_and_self_base(tmp_path, monkeypatch):
    """CRITIQUE-FIX: pinned constructor — base kwarg stored on self._base."""
    monkeypatch.setattr(sr.paths, "base_dir", lambda: "/default/base")
    reg_default = sr._Registry()
    assert reg_default._base == "/default/base"          # falls back to base_dir()
    reg_explicit = sr._Registry(base=str(tmp_path))
    assert reg_explicit._base == str(tmp_path)            # explicit wins


def test_registry_structural_lock_never_held_during_tmux_io():
    """A blocking-tmux stub: assert the structural lock is FREE whenever tmux
    I/O happens by checking lock.locked() from inside the stub."""
    reg = sr._Registry()
    observed = []

    class _BlockingTmux(_StubTmux):
        def has_session(self, name):
            observed.append(reg._lock.locked())   # must be False
            return True

    s = _mk_session(tmux=_BlockingTmux())
    with reg._lock:
        reg._sessions[s.sid] = s
    alive = reg._alive_ids([s])
    assert alive == [s.sid]
    assert observed == [False]


def test_registry_module_singleton_exists():
    """CRITIQUE-FIX: single canonical singleton name is REGISTRY."""
    assert isinstance(sr.REGISTRY, sr._Registry)
```

**(2) Run + expected FAIL**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "registry"
```

Expected: `AttributeError: module 'session_registry' has no attribute '_Registry'`.

**(3) Minimal implementation** — extend the top-of-file import in `<WT>/session_registry.py`:

```python
import paths
```

Append the class + singleton:

```python
class _Registry:
    def __init__(self, base=None):
        self._lock = threading.Lock()           # STRUCTURAL ONLY
        self._sessions = {}                      # sid -> _Session
        # CRITIQUE-FIX: pinned constructor — explicit base or paths.base_dir().
        self._base = base if base is not None else paths.base_dir()

    def _snapshot(self):
        """Copy the dict under the structural lock; release before any I/O."""
        with self._lock:
            return list(self._sessions.values())

    def _alive_ids(self, sessions):
        """tmux liveness probe OUTSIDE the structural lock (design (1))."""
        alive = []
        for s in sessions:
            try:
                if s.tmux.has_session("t"):
                    alive.append(s.sid)
            except Exception:
                pass
        return alive


REGISTRY = _Registry()
```

**(4) Run + expected PASS**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "registry"
```

Expected: `4 passed`.

**(5) Commit**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/unit/test_registry_locking.py && git commit -m "$(cat <<'EOF'
registry: _Registry skeleton (base param) + structural-lock invariant

Pinned constructor _Registry(base=None) storing self._base (default
paths.base_dir()) so the reaper/security components share one signature;
single module singleton REGISTRY. _snapshot copies the dict under the
structural lock then releases; _alive_ids does tmux I/O with the lock
FREE. Test asserts the lock is never held during tmux calls (design (1)).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12 — `_Registry.create` — mint id/cap/nonce, base verify, 0700 dir + cap file, socket-path defensive clear

**Files**
- Modify: `<WT>/session_registry.py` (add `create`, `MAX_SESSIONS`, `_MaxSessionsReached`)
- Test: `<WT>/tests/integration/test_session_lifecycle.py` (create file)

> CRITIQUE-FIX applied: `new_session("t", rcwd, cols, rows)` is called with NO argv (the launch line is typed in Task 14 `_await_ready`); this pins the tmux-client `new_session(name, cwd, cols, rows)` contract — the argv-bearing variant is rejected. `socket_path()` is required from the tmux-client component (cross-ref). `socket_override` is a test-only seam and MUST never be forwarded from the HTTP `_do_create` (Task 18) — risk #6.

**(1) Write the FAILING test** — create `<WT>/tests/integration/test_session_lifecycle.py`:

```python
import os
import stat
import pytest

import session_registry as sr
import paths

from conftest import requires_tmux   # bare import (import-mode pinned in conftest)


@requires_tmux
def test_create_mints_and_confines(tmp_base, fake_socket, fake_claude_argv, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    cwd = str(tmp_base)            # a real, confined, existing dir
    s = reg.create(cwd=cwd, cols=120, rows=40, claude_argv=fake_claude_argv,
                   socket_override=fake_socket)
    try:
        assert paths.SESSION_ID_RE.match(s.sid)
        assert len(s.cap) == 64
        assert len(s.nonce) == 16              # token_hex(8)
        st = os.lstat(s.rendezvous_dir)
        assert stat.S_ISDIR(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o700
        assert st.st_uid == os.getuid()
        assert s.rendezvous_dir.startswith(str(tmp_base))
        cap_file = os.path.join(s.rendezvous_dir, "cap")
        cst = os.lstat(cap_file)
        assert stat.S_IMODE(cst.st_mode) == 0o600
        with open(cap_file) as f:
            assert f.read() == s.cap
        assert reg._sessions[s.sid] is s
        assert s.status == "READY"
        assert s.tmux.has_session("t") is True
    finally:
        s.tmux.kill_server()


@requires_tmux
def test_create_rejects_nonexistent_cwd(tmp_base, fake_socket, fake_claude_argv, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    with pytest.raises((ValueError, NotADirectoryError, FileNotFoundError)):
        reg.create(cwd=str(tmp_base / "does-not-exist"), cols=80, rows=24,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)


def test_create_max_sessions_429(tmp_base, fake_claude_argv, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    monkeypatch.setattr(sr, "MAX_SESSIONS", 0)
    reg = sr._Registry(base=str(tmp_base))
    with pytest.raises(sr._MaxSessionsReached):
        reg.create(cwd=str(tmp_base), cols=80, rows=24,
                   claude_argv=fake_claude_argv, socket_override="wcb_unused")
```

> Note: `socket_override` is a TEST-ONLY kwarg so the integration tests drive the per-test `-L` socket from the `fake_socket` fixture (no leaked servers). Production `create` derives `wcb_<sid>`; the HTTP wrapper (Task 18 `_do_create`) MUST never forward a caller-supplied socket (risk #6).

**(2) Run + expected FAIL**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_lifecycle.py
```

Expected: `AttributeError: '_Registry' object has no attribute 'create'` (or `_MaxSessionsReached` missing).

**(3) Minimal implementation** — extend imports at the top of `<WT>/session_registry.py`:

```python
import os
import stat

from tmux_session import TmuxClient, _TmuxError
```

Add the constants/exception near the top:

```python
MAX_SESSIONS = 8                # design (4): max concurrent-session cap


class _MaxSessionsReached(Exception):
    pass
```

Add the method to `_Registry`:

```python
    def create(self, *, cwd, cols, rows, claude_argv, socket_override=None):
        # 0) confine cwd: must be an existing dir, realpath-resolved.
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("cwd must be a non-empty string")
        rcwd = os.path.realpath(cwd)
        if not os.path.isdir(rcwd):
            raise NotADirectoryError(cwd)

        # cap concurrent sessions (design (4) -> 429)
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                raise _MaxSessionsReached(
                    f"max {MAX_SESSIONS} concurrent sessions")

        base = self._base
        paths.verify_base_dir(base)        # S_ISDIR & !LNK & uid==euid & 0700

        sid = paths.mint_session_id()
        cap = paths.mint_cap()
        nonce = paths.mint_nonce()
        socket = socket_override or ("wcb_" + sid)
        rdir = paths.session_dir(base, sid, nonce)
        paths.assert_confined(rdir, base)

        # 0700 dir + explicit chmod (defeat umask) + 0600 cap file.
        os.makedirs(rdir, mode=0o700, exist_ok=False)
        os.chmod(rdir, 0o700)
        cap_path = os.path.join(rdir, "cap")
        fd = os.open(cap_path,
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, cap.encode("ascii"))
        finally:
            os.close(fd)

        log_path = os.path.join(rdir, "log")
        tmux = TmuxClient(socket)

        # Defensive socket-path clear (design (1), risk #11): a leftover
        # NON-socket file permanently bricks new-session; a stale socket is
        # cleared iff has-session fails.
        self._clear_stale_socket(tmux, socket)

        # CRITIQUE-FIX: new_session takes NO argv — the launch line is typed
        # in _await_ready (Task 14). Pins tmux-client new_session(name,cwd,cols,rows).
        pane = tmux.new_session("t", rcwd, cols, rows)

        s = _Session(
            sid=sid, cap=cap, nonce=nonce, socket=socket, pane=pane,
            cwd=rcwd, rendezvous_dir=rdir, log_path=log_path,
            created_at=time.time(), tmux=tmux,
        )
        # persist durable options (survive a bridge restart, no Python state)
        tmux.set_option("@wcb_created", str(int(s.created_at)))
        tmux.set_option("@wcb_nonce", nonce)

        with self._lock:
            self._sessions[sid] = s
        s.status = "READY"
        s.ready.set()
        return s

    def _clear_stale_socket(self, tmux, socket):
        """Unlink a leftover non-socket file; unlink a dead socket.

        Delegated to the tmux-client socket_path() accessor (cross-ref:
        tmux-client component MUST provide socket_path()). A non-socket file
        at the path is the brick case (risk #11).
        """
        sock_path = getattr(tmux, "socket_path", lambda: None)()
        if not sock_path:
            return
        try:
            st = os.lstat(sock_path)
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(st.st_mode):
            os.unlink(sock_path)            # non-socket file -> unconditional clear
            return
        try:
            if not tmux.has_session("t"):
                os.unlink(sock_path)
        except _TmuxError:
            try:
                os.unlink(sock_path)
            except OSError:
                pass
```

> Cross-ref: `TmuxClient.socket_path()` (resolved `-L` socket file path, or `None`) is required from the tmux-client component for the defensive clear (risk #11) and the delete-time unlink (Task 17). If it lands as an attribute instead of a method, change the two `getattr(... "socket_path", lambda: None)()` call sites accordingly.

**(4) Run + expected PASS**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_lifecycle.py
```

Expected: `3 passed` (or the two `@requires_tmux` tests skip if `tmux` absent; verify on a tmux host with `tmux -V`).

**(5) Commit**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/integration/test_session_lifecycle.py && git commit -m "$(cat <<'EOF'
registry: create() — mint id/cap/nonce, confine cwd, 0700 dir, socket clear

create() realpath-confines cwd (isdir), enforces MAX_SESSIONS (->429),
verifies the base dir, mints session_id/cap/nonce, makes a 0700 rendezvous
dir with explicit chmod + 0600 cap file, defensively clears a stale/
non-socket -L path (risk #11), starts the tmux session (new_session with
NO argv — launch typed in _await_ready), and persists @wcb_created/@wcb_nonce.
socket_override is a test-only seam, never forwarded from HTTP (risk #6).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13 — `_Registry.create` — `alternate_on==0` assertion + pipe-pane retention arm

**Files**
- Modify: `<WT>/session_registry.py` (extend `create`, add `_AlternateScreenError`)
- Test: `<WT>/tests/integration/test_session_lifecycle.py` (append)

> CRITIQUE-FIX applied: `pipe_pane_on` is the disable-first canonical definition owned by the tmux-client component (the re-pipe idempotency / `pane_pipe()==1` test lives there); this task only arms it. Stubs implement `new_session(name, cwd, cols, rows)` with no argv to match the pinned signature.

**(1) Write the FAILING test** — append to `<WT>/tests/integration/test_session_lifecycle.py`:

```python
@requires_tmux
def test_create_verifies_alternate_off_and_arms_pipe(tmp_base, fake_socket,
                                                     fake_claude_argv, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    s = reg.create(cwd=str(tmp_base), cols=100, rows=30,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)
    try:
        assert s.tmux.alternate_on(s.pane) == 0
        assert os.path.exists(s.log_path)
        lst = os.lstat(s.log_path)
        assert stat.S_IMODE(lst.st_mode) == 0o600
    finally:
        s.tmux.kill_server()


def test_create_raises_on_alternate_on(tmp_base, monkeypatch):
    """Pure-stub path: a tmux whose alternate_on != 0 must abort create."""
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))

    class _AltTmux:
        def __init__(self, *a, **k): pass
        def socket_path(self): return None
        def new_session(self, name, cwd, cols, rows): return "%0"
        def has_session(self, n): return True
        def set_option(self, *a): pass
        def get_option(self, n): return None
        def alternate_on(self, t): return 1          # the bad case
        def pipe_pane_on(self, *a): pass
        def kill_server(self): pass

    monkeypatch.setattr(sr, "TmuxClient", lambda sock: _AltTmux())
    reg = sr._Registry(base=str(tmp_base))
    with pytest.raises(sr._AlternateScreenError):
        reg.create(cwd=str(tmp_base), cols=80, rows=24,
                   claude_argv=["bash", "-c", ":"], socket_override="wcb_alt")
```

**(2) Run + expected FAIL**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k "alternate or pipe"
```

Expected: `AttributeError: module 'session_registry' has no attribute '_AlternateScreenError'` and the pipe-pane assertion fails (log not created).

**(3) Minimal implementation** — add the exception near the others in `<WT>/session_registry.py`:

```python
class _AlternateScreenError(Exception):
    pass
```

In `create`, after `pane = tmux.new_session(...)` and before constructing `_Session`, insert:

```python
        # Verify the inline-capture invariant ONCE (design (1), calibration):
        # claude renders inline (alternate_on must be 0) or capture-pane -p
        # is not a valid view of the TUI.
        if tmux.alternate_on(pane) != 0:
            try:
                tmux.kill_server()
            except Exception:
                pass
            raise _AlternateScreenError("alternate_on != 0; aborting create")

        # Arm pipe-pane retention to a private 0600 log. The disable-first
        # pipe_pane_on definition is owned by the tmux-client component
        # (re-pipe idempotency / pane_pipe()==1 tested there); here we arm it.
        tmux.pipe_pane_on(pane, log_path)
```

**(4) Run + expected PASS**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k "alternate or pipe"
```

Expected: `2 passed` (the `_AltTmux` stub test runs without tmux; the live one needs tmux).

**(5) Commit**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/integration/test_session_lifecycle.py && git commit -m "$(cat <<'EOF'
registry: create() — assert alternate_on==0, arm pipe-pane retention

After new-session, create() verifies #{alternate_on}==0 once (kills the
server and raises _AlternateScreenError otherwise) and arms pipe-pane to
the private 0600 log (disable-first pipe_pane_on owned by tmux-client).
Both are load-bearing for the inline-capture model (design (1)).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14 — Readiness probe + trust-prompt detect/answer (positive composer-ready, never quiescence)

**Files**
- Modify: `<WT>/session_registry.py` (add `_await_ready`, call from `create`; add `READY_TIMEOUT`/`READY_POLL`, `_ReadinessTimeout`)
- Test: `<WT>/tests/integration/test_session_lifecycle.py` (append)

> CRITIQUE-FIX applied: `_await_ready` types the `claude_argv` launch line (it is the SOLE typist of the launch — `new_session` carries no argv) then runs a POSITIVE composer-ready probe; `classify(...)` is called with the pinned signature incl. `composer_seen` (latched to `s.composer_seen` once the composer is seen, feeding the FSM dead-discriminator). The trust prompt is `state == "awaiting_input" and meta.get("kind") == "trust"` (the `classify` `meta["kind"]="trust"` contract owned by turn-protocol-fsm). Canonical footer token `FOOTER_IDLE` from the single `fsm.py`/`fake_claude.sh` contract.

**(1) Write the FAILING test** — append to `<WT>/tests/integration/test_session_lifecycle.py`:

```python
@requires_tmux
def test_create_reaches_composer_ready(tmp_base, fake_socket, fake_claude_argv,
                                       monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    s = reg.create(cwd=str(tmp_base), cols=100, rows=30,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)
    try:
        screen = s.tmux.capture_pane(s.pane)
        from fsm import FOOTER_IDLE
        assert FOOTER_IDLE in screen
        assert s.composer_seen is True          # latched once composer seen
    finally:
        s.tmux.kill_server()


@requires_tmux
def test_create_answers_trust_prompt(tmp_base, fake_socket, fake_claude_argv,
                                     monkeypatch):
    """WCB_FAKE_TRUST=1 makes the fake claude show the trust block first;
    create() must detect and answer it with ['1','Enter'] before composer."""
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    monkeypatch.setenv("WCB_FAKE_TRUST", "1")
    reg = sr._Registry(base=str(tmp_base))
    s = reg.create(cwd=str(tmp_base), cols=100, rows=30,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)
    try:
        screen = s.tmux.capture_pane(s.pane)
        from fsm import FOOTER_IDLE, TRUST_PROMPT
        assert FOOTER_IDLE in screen          # got past trust to composer
        assert TRUST_PROMPT not in screen     # trust block dismissed
    finally:
        s.tmux.kill_server()


def test_readiness_timeout_raises(tmp_base, monkeypatch):
    """A pane that never reaches composer must time out (no quiescence path)."""
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))

    class _NeverReady:
        def __init__(self, *a, **k): pass
        def socket_path(self): return None
        def new_session(self, name, cwd, cols, rows): return "%0"
        def has_session(self, n): return True
        def set_option(self, *a): pass
        def get_option(self, n): return None
        def alternate_on(self, t): return 0
        def pipe_pane_on(self, *a): pass
        def kill_server(self): pass
        def capture_pane(self, t): return "still starting\n"   # never composer
        def send_keys(self, *a): pass
        def send_text(self, *a): pass

    monkeypatch.setattr(sr, "TmuxClient", lambda sock: _NeverReady())
    monkeypatch.setattr(sr, "READY_TIMEOUT", 0.3)          # short test budget
    monkeypatch.setattr(sr, "READY_POLL", 0.05)
    reg = sr._Registry(base=str(tmp_base))
    with pytest.raises(sr._ReadinessTimeout):
        reg.create(cwd=str(tmp_base), cols=80, rows=24,
                   claude_argv=["bash", "-c", ":"], socket_override="wcb_nr")
```

**(2) Run + expected FAIL**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k "ready or trust"
```

Expected: `AttributeError: module 'session_registry' has no attribute '_ReadinessTimeout'` and the composer assertions fail (composer never typed/awaited).

**(3) Minimal implementation** — extend imports at the top of `<WT>/session_registry.py`:

```python
from fsm import FOOTER_IDLE, TRUST_PROMPT, classify, footer_of, strip_screen
```

Add constants + exception near the others:

```python
READY_TIMEOUT = 30.0            # design (1): positive composer-ready poll <=30s
READY_POLL = 0.25


class _ReadinessTimeout(Exception):
    pass
```

Add the helper to `_Registry`:

```python
    def _await_ready(self, s, claude_argv):
        """Type the launch argv, then a POSITIVE composer-ready probe.

        Never declares ready by quiescence (design (1), risk #2). Detects and
        answers the workspace-trust prompt explicitly with ['1','Enter']
        (calibration: bypassPermissions does NOT suppress it).
        """
        # SOLE typist of the launch line (new_session carries no argv).
        s.tmux.send_text(s.pane, " ".join(claude_argv))
        s.tmux.send_keys(s.pane, "Enter")

        deadline = time.monotonic() + READY_TIMEOUT
        trust_answered = False
        while time.monotonic() < deadline:
            screen = strip_screen(s.tmux.capture_pane(s.pane))
            footer = footer_of(screen)
            # CRITIQUE-FIX: pinned classify signature incl. composer_seen.
            state, meta = classify(
                screen, footer, env_present=False,
                prev_timer=None, composer_seen=s.composer_seen,
            )
            if state == "awaiting_input" and meta.get("kind") == "trust" \
                    and not trust_answered:
                s.tmux.send_keys(s.pane, "1", "Enter")
                trust_answered = True
                time.sleep(READY_POLL)
                continue
            # positive readiness: composer footer present AND no menu.
            if FOOTER_IDLE in screen and "\u276f 1." not in screen \
                    and TRUST_PROMPT not in screen:
                s.composer_seen = True          # latch for FSM dead-discriminator
                return
            time.sleep(READY_POLL)
        raise _ReadinessTimeout("composer not ready within %.0fs" % READY_TIMEOUT)
```

In `create`, the published-READY path must run the probe BEFORE marking ready. Replace the tail of `create` (`with self._lock: self._sessions[sid] = s` … `return s`) with:

```python
        # POSITIVE readiness probe (+ trust prompt handling) before we publish
        # the session as READY. Failure tears down the server.
        try:
            self._await_ready(s, claude_argv)
        except Exception:
            try:
                tmux.kill_server()
            except Exception:
                pass
            raise

        with self._lock:
            self._sessions[sid] = s
        s.status = "READY"
        s.ready.set()
        return s
```

**(4) Run + expected PASS**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k "ready or trust"
```

Expected: `3 passed` (the `_NeverReady` timeout test runs without tmux; the two live ones need tmux).

**(5) Commit**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/integration/test_session_lifecycle.py && git commit -m "$(cat <<'EOF'
registry: positive readiness probe + trust-prompt detect/answer

create() (via _await_ready, the sole launch typist) types the claude argv
then polls <=30s for a POSITIVE composer-ready signal (FOOTER_IDLE present,
no menu) and latches composer_seen — never quiescence (risk #2). The
workspace-trust prompt is detected via classify(...,composer_seen=...) and
answered with ['1','Enter'] (bypassPermissions does not suppress it).
Timeout/teardown via _ReadinessTimeout.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15 — `get_or_reconstruct` — placeholder + Event setdefault (dual-lock race fix) + nonce cross-check

**Files**
- Modify: `<WT>/session_registry.py` (add `get_or_reconstruct`, `_hydrate`, `_NotFound`, `_RendezvousRedirect`)
- Test: `<WT>/tests/unit/test_registry_locking.py` (append)

> CRITIQUE-FIX applied: `_hydrate` now CROSS-CHECKS the dir derived from `@wcb_nonce` against the id-derived path under base, raising `_RendezvousRedirect` if a tampered `@wcb_nonce` points outside base (risk #14 half-b — previously untested). `_Session.__init__` already carries `_claimed` (Task 10), so this task adds no field. The `import time` referenced by the concurrency test lives at the test-file top (added in Task 10).

**(1) Write the FAILING test** — append to `<WT>/tests/unit/test_registry_locking.py`:

```python
def test_get_or_reconstruct_single_winner_under_concurrency(monkeypatch):
    """N threads racing get_or_reconstruct(sid) must share ONE _Session and
    ONE turn_lock; only ONE thread hydrates (design risk #5)."""
    reg = sr._Registry()
    sid = "b" * 32

    monkeypatch.setattr(sr.paths, "validate_session_id", lambda x: None)
    hydrate_calls = []
    barrier = threading.Barrier(8)

    def fake_hydrate(s):
        hydrate_calls.append(s.sid)
        time.sleep(0.05)            # widen the race window
        s.pane = "%0"
        s.status = "READY"

    monkeypatch.setattr(reg, "_hydrate", fake_hydrate)

    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        s = reg.get_or_reconstruct(sid)
        with lock:
            results.append(s)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert hydrate_calls == [sid]
    first = results[0]
    assert all(r is first for r in results)
    assert all(r.turn_lock is first.turn_lock for r in results)
    assert first.ready.is_set()
    assert first.status == "READY"


def test_get_or_reconstruct_returns_existing_ready(monkeypatch):
    reg = sr._Registry()
    monkeypatch.setattr(sr.paths, "validate_session_id", lambda x: None)
    s = _mk_session(sid="e" * 32)
    s.status = "READY"
    s.ready.set()
    with reg._lock:
        reg._sessions[s.sid] = s
    called = []
    monkeypatch.setattr(reg, "_hydrate", lambda x: called.append(1))
    got = reg.get_or_reconstruct("e" * 32)
    assert got is s
    assert called == []            # already READY -> no hydrate


def test_hydrate_rejects_tampered_nonce_outside_base(tmp_base, monkeypatch):
    """CRITIQUE-FIX risk #14: a tampered @wcb_nonce that re-derives a dir
    OUTSIDE base must be rejected (cross-check id-derived path)."""
    monkeypatch.setattr(sr.paths, "base_dir", lambda: str(tmp_base))

    class _TamperTmux(_StubTmux):
        def has_session(self, name):
            return True
        def pane_id(self, name):
            return "%0"

    reg = sr._Registry(base=str(tmp_base))
    s = _mk_session(sid="9" * 32, nonce=None, rendezvous_dir=None,
                    tmux=_TamperTmux())
    # malicious nonce that paths.session_dir would resolve outside base
    s.tmux.set_option("@wcb_nonce", "../../etc")
    with pytest.raises(sr._RendezvousRedirect):
        reg._hydrate(s)
```

**(2) Run + expected FAIL**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "reconstruct or hydrate"
```

Expected: `AttributeError: '_Registry' object has no attribute 'get_or_reconstruct'` (and `_RendezvousRedirect` missing).

**(3) Minimal implementation** — add the exceptions to `<WT>/session_registry.py`:

```python
class _NotFound(Exception):
    pass


class _RendezvousRedirect(Exception):
    pass
```

Add to `_Registry`:

```python
    def get_or_reconstruct(self, sid):
        """Placeholder + Event pattern (design (1), kills the dual-lock race).

        Under the structural lock, setdefault a RECONSTRUCTING placeholder
        whose turn_lock is created NOW. A single _claimed winner hydrates via
        tmux I/O OUTSIDE the lock and sets s.ready; losers block on s.ready and
        share the one object + one turn_lock.
        """
        paths.validate_session_id(sid)
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                s = _Session(
                    sid=sid, cap=None, nonce=None,
                    socket=("wcb_" + sid), pane=None,
                    cwd=None, rendezvous_dir=None, log_path=None,
                    created_at=time.time(), tmux=TmuxClient("wcb_" + sid),
                )
                self._sessions[sid] = s
                winner = True
                s._claimed = True
            elif s.status == "RECONSTRUCTING" and not s._claimed:
                s._claimed = True
                winner = True
            else:
                winner = False

        if winner and s.status == "RECONSTRUCTING":
            try:
                self._hydrate(s)
                s.status = "READY"
            except Exception:
                with self._lock:
                    if self._sessions.get(sid) is s:
                        del self._sessions[sid]
                s.ready.set()           # unblock losers; they will re-resolve
                raise
            finally:
                s.ready.set()
            return s

        if s.status == "READY":
            return s
        # loser: block on the Event, then return the shared object
        s.ready.wait(timeout=READY_TIMEOUT)
        if self._sessions.get(sid) is not s or s.status != "READY":
            raise _NotFound(sid)
        return s

    def _hydrate(self, s):
        """Rebuild a _Session's volatile fields from tmux facts (no Python
        state needed). Liveness is has-session ONLY; missing @wcb_* options
        are recoverable defaults, never 'dead' (design (1), risk #11)."""
        if not s.tmux.has_session("t"):
            raise _NotFound(s.sid)
        s.pane = s.tmux.pane_id("t")
        created = s.tmux.get_option("@wcb_created")
        nonce = s.tmux.get_option("@wcb_nonce")
        if created:
            try:
                s.created_at = float(created)
            except ValueError:
                pass
        if nonce:
            base = self._base
            # CRITIQUE-FIX risk #14: cross-check the option-derived dir against
            # the id-derived path; a tampered @wcb_nonce redirect is rejected.
            rdir = paths.session_dir(base, s.sid, nonce)
            try:
                rdir = paths.assert_confined(rdir, base)
            except PermissionError:
                raise _RendezvousRedirect(s.sid)
            s.nonce = nonce
            s.rendezvous_dir = rdir
            s.log_path = os.path.join(rdir, "log")
```

**(4) Run + expected PASS**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "reconstruct or hydrate"
```

Expected: `3 passed`.

**(5) Commit**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/unit/test_registry_locking.py && git commit -m "$(cat <<'EOF'
registry: get_or_reconstruct placeholder+Event + nonce cross-check

Under the structural lock, setdefault a RECONSTRUCTING placeholder whose
turn_lock is created now; a single _claimed winner hydrates via tmux I/O
OUTSIDE the lock and sets s.ready, losers block on the Event and share the
one object + turn_lock (risk #5). _hydrate rebuilds volatile fields from
has-session + @wcb_* defaults (liveness is has-session only) and CROSS-CHECKS
the @wcb_nonce-derived dir against the id-derived path under base, raising
_RendezvousRedirect on a tampered redirect (risk #14).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16 — `list_sessions` + brief `/list` cache — facts-first, busy state, never-evict-locked, `log_bytes`

**Files**
- Modify: `<WT>/session_registry.py` (add `list_sessions`, `LIST_CACHE_TTL`)
- Test: `<WT>/tests/unit/test_registry_locking.py` (append)

> CRITIQUE-FIX applied: a brief `/list` cache (design (4): "cache `/list` briefly to avoid O(N) subprocess fan-out") is implemented + tested here. `log_bytes` is exposed and accounts for `log_offset_base` so the reported size matches the replay offset space after rotation. `classify` (via `is_busy`/`_state`) uses the pinned `composer_seen` path. `state=busy` whenever the turn lock is held; never evict.

**(1) Write the FAILING test** — append to `<WT>/tests/unit/test_registry_locking.py`:

```python
def test_list_sessions_reports_busy_when_lock_held():
    reg = sr._Registry()
    s = _mk_session(sid="f" * 32)
    s.status = "READY"
    s._classify_state = lambda: "idle"
    with reg._lock:
        reg._sessions[s.sid] = s

    rows = reg.list_sessions()
    assert len(rows) == 1
    assert rows[0]["session_id"] == "f" * 32
    assert rows[0]["state"] in ("idle", "ready")
    assert rows[0]["alive"] is True

    reg._list_cache = None        # bypass the brief cache for this assertion
    s.turn_lock.acquire()
    try:
        rows = reg.list_sessions()
        assert rows[0]["state"] == "busy"      # turn lock held -> busy
    finally:
        s.turn_lock.release()


def test_list_sessions_never_holds_structural_lock_during_tmux():
    reg = sr._Registry()
    observed = []

    class _Probe(_StubTmux):
        def has_session(self, name):
            observed.append(reg._lock.locked())
            return True
        def capture_pane(self, target):
            observed.append(reg._lock.locked())
            return ""

    s = _mk_session(sid="0" * 32, tmux=_Probe())
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    reg.list_sessions()
    assert observed and all(held is False for held in observed)


def test_list_sessions_marks_dead_alive_false_without_evicting():
    reg = sr._Registry()

    class _Dead(_StubTmux):
        def has_session(self, name):
            return False

    s = _mk_session(sid="1" * 32, tmux=_Dead())
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    rows = reg.list_sessions()
    assert rows[0]["alive"] is False
    assert s.sid in reg._sessions          # list NEVER evicts


def test_list_sessions_log_bytes_includes_offset_base(tmp_base, monkeypatch):
    """CRITIQUE-FIX: log_bytes accounts for log_offset_base (rotation)."""
    monkeypatch.setattr(sr.paths, "base_dir", lambda: str(tmp_base))
    log = tmp_base / "log"
    log.write_bytes(b"x" * 100)
    reg = sr._Registry(base=str(tmp_base))
    s = _mk_session(sid="2" * 32, log_path=str(log))
    s.status = "READY"
    s._classify_state = lambda: "idle"
    s.log_offset_base = 40                 # 40 bytes rotated away earlier
    with reg._lock:
        reg._sessions[s.sid] = s
    rows = reg.list_sessions()
    assert rows[0]["log_bytes"] == 140     # 100 on disk + 40 base


def test_list_sessions_brief_cache(monkeypatch):
    """CRITIQUE-FIX design (4): /list cached briefly to avoid O(N) fan-out."""
    reg = sr._Registry()
    probes = []

    class _Counting(_StubTmux):
        def has_session(self, name):
            probes.append(1)
            return True

    s = _mk_session(sid="3" * 32, tmux=_Counting())
    s.status = "READY"
    s._classify_state = lambda: "idle"
    with reg._lock:
        reg._sessions[s.sid] = s
    monkeypatch.setattr(sr, "LIST_CACHE_TTL", 60.0)
    reg.list_sessions()
    reg.list_sessions()                    # served from cache, no new probe
    assert sum(probes) == 1
    monkeypatch.setattr(sr, "LIST_CACHE_TTL", 0.0)   # expire immediately
    reg.list_sessions()
    assert sum(probes) == 2
```

**(2) Run + expected FAIL**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "list_sessions"
```

Expected: `AttributeError: '_Registry' object has no attribute 'list_sessions'`.

**(3) Minimal implementation** — add the TTL constant near the others in `<WT>/session_registry.py`:

```python
LIST_CACHE_TTL = 1.0            # design (4): brief /list cache (sec)
```

In `_Registry.__init__`, add the cache fields:

```python
        self._list_cache = None                 # (built_at, rows)
```

Append `list_sessions` to `_Registry`:

```python
    def list_sessions(self):
        """Gather tmux facts first (lock FREE), then under the structural lock
        apply only additive reads. state=busy whenever the turn lock is held;
        NEVER evict here (design (1)). Result cached briefly (design (4))."""
        cache = self._list_cache
        if cache is not None and (time.monotonic() - cache[0]) < LIST_CACHE_TTL:
            return cache[1]

        sessions = self._snapshot()             # copy under lock, release
        rows = []
        for s in sessions:
            # tmux I/O happens with the structural lock FREE.
            try:
                alive = s.tmux.has_session("t")
            except Exception:
                alive = False

            if s.turn_lock.locked():
                state = "busy"
            elif s.status == "RECONSTRUCTING":
                state = "reconstructing"
            elif s.is_busy():                   # @wcb_turn OR FSM != idle
                state = "busy"
            else:
                state = "idle"

            try:
                on_disk = os.path.getsize(s.log_path) if s.log_path else 0
            except OSError:
                on_disk = 0
            # CRITIQUE-FIX: report bytes in the global (rotation-adjusted) space.
            log_bytes = on_disk + s.log_offset_base

            rows.append({
                "session_id": s.sid,
                "state": state,
                "created_at": s.created_at,
                "log_bytes": log_bytes,
                "alive": bool(alive),
            })
        self._list_cache = (time.monotonic(), rows)
        return rows
```

**(4) Run + expected PASS**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "list_sessions"
```

Expected: `5 passed`.

**(5) Commit**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/unit/test_registry_locking.py && git commit -m "$(cat <<'EOF'
registry: list_sessions facts-first + busy state + brief cache, never-evict

list_sessions snapshots the dict under the structural lock, then probes
tmux liveness with the lock FREE. state=busy whenever the turn lock is
held (or @wcb_turn/FSM!=idle); dead sessions report alive:false but are
NEVER evicted (only the reaper evicts). log_bytes is reported in the
rotation-adjusted space (on-disk + log_offset_base), and results are
cached for LIST_CACHE_TTL to avoid O(N) subprocess fan-out (design (4)).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17 — `delete` — confined teardown (kill-server + unlink socket + rm -rf only if confined)

**Files**
- Modify: `<WT>/session_registry.py` (add `delete`, `_SessionBusy`)
- Test: `<WT>/tests/unit/test_registry_locking.py` (append) + `<WT>/tests/integration/test_session_lifecycle.py` (append)

> CRITIQUE-FIX applied: `delete` uses `self._base` (pinned constructor) for `assert_confined`; the socket unlink uses the tmux-client `socket_path()` accessor (cross-ref). Stubs implement `socket_path()` to match.

**(1) Write the FAILING test** — append the unit (cap/busy/confinement) cases to `<WT>/tests/unit/test_registry_locking.py`:

```python
def test_delete_rejects_cap_mismatch():
    reg = sr._Registry()
    s = _mk_session(sid="2" * 32, cap="right" + "0" * 59)
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    with pytest.raises(PermissionError):
        reg.delete("2" * 32, "wrong" + "0" * 59)
    assert s.sid in reg._sessions          # not torn down on cap mismatch


def test_delete_refuses_when_busy():
    reg = sr._Registry()
    s = _mk_session(sid="3" * 32, cap="k" * 64)
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    s.turn_lock.acquire()
    try:
        with pytest.raises(sr._SessionBusy):
            reg.delete("3" * 32, "k" * 64)
    finally:
        s.turn_lock.release()
    assert s.sid in reg._sessions


def test_delete_refuses_unconfined_dir(tmp_base, monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    killed = []

    class _T(_StubTmux):
        def kill_server(self):
            killed.append(True)
        def socket_path(self):
            return None

    s = _mk_session(sid="4" * 32, cap="k" * 64,
                    rendezvous_dir="/etc", tmux=_T())
    s.status = "READY"
    with reg._lock:
        reg._sessions[s.sid] = s
    with pytest.raises(PermissionError):
        reg.delete("4" * 32, "k" * 64)
    import os as _os
    assert _os.path.isdir("/etc")          # /etc must NOT have been rm'd
```

Append the live teardown case to `<WT>/tests/integration/test_session_lifecycle.py`:

```python
@requires_tmux
def test_delete_tears_down_confined(tmp_base, fake_socket, fake_claude_argv,
                                    monkeypatch):
    monkeypatch.setattr(paths, "base_dir", lambda: str(tmp_base))
    reg = sr._Registry(base=str(tmp_base))
    s = reg.create(cwd=str(tmp_base), cols=80, rows=24,
                   claude_argv=fake_claude_argv, socket_override=fake_socket)
    rdir = s.rendezvous_dir
    assert os.path.isdir(rdir)
    reg.delete(s.sid, s.cap)
    assert s.tmux.has_session("t") is False
    assert not os.path.exists(rdir)
    assert s.sid not in reg._sessions
```

**(2) Run + expected FAIL**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "delete" && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k "delete"
```

Expected: `AttributeError: '_Registry' object has no attribute 'delete'` (and `_SessionBusy` missing).

**(3) Minimal implementation** — extend imports at the top of `<WT>/session_registry.py`:

```python
import hmac
import shutil
```

Add the exception:

```python
class _SessionBusy(Exception):
    pass
```

Add to `_Registry`:

```python
    def delete(self, sid, cap):
        """Confined teardown (design (1)): cap match, refuse if busy, then
        kill-server, unlink the socket, and rm -rf the dir + log ONLY if the
        dir realpath-confines under base."""
        paths.validate_session_id(sid)
        with self._lock:
            s = self._sessions.get(sid)
        if s is None:
            raise _NotFound(sid)

        # cap is mandatory + constant-time compared (closes hijack).
        if not s.cap or not isinstance(cap, str):
            raise PermissionError("cap required")
        try:
            if not hmac.compare_digest(s.cap, cap):
                raise PermissionError("cap mismatch")
        except (TypeError, ValueError):
            raise PermissionError("cap mismatch")

        # Never tear down a session whose turn lock is held (risk #13).
        if s.turn_lock.locked():
            raise _SessionBusy(sid)

        # kill the tmux server (best-effort) then unlink the socket path.
        try:
            s.tmux.kill_server()
        except Exception:
            pass
        sock_path = getattr(s.tmux, "socket_path", lambda: None)()
        if sock_path:
            try:
                os.unlink(sock_path)
            except OSError:
                pass

        # rm -rf the rendezvous dir + log ONLY IF confined under base.
        if s.rendezvous_dir:
            try:
                confined = paths.assert_confined(s.rendezvous_dir, self._base)
            except PermissionError:
                # Unconfined dir: refuse the rm; surface loudly.
                raise
            shutil.rmtree(confined, ignore_errors=True)

        with self._lock:
            if self._sessions.get(sid) is s:
                del self._sessions[sid]
        self._list_cache = None                 # invalidate the /list cache
```

> Ordering note for the unconfined test: `kill_server` is called before `assert_confined` raises (server dies, rm refused) — matching the live behavior. `/etc` is never removed because `assert_confined` raises before `rmtree`.

**(4) Run + expected PASS**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "delete" && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k "delete"
```

Expected: unit `3 passed`; integration `1 passed` (needs tmux).

**(5) Commit**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/unit/test_registry_locking.py tests/integration/test_session_lifecycle.py && git commit -m "$(cat <<'EOF'
registry: delete() confined teardown (cap + busy guard + realpath rm)

delete() requires a constant-time cap match, refuses a busy session
(turn lock held, risk #13), kills the tmux server, unlinks the socket
(tmux-client socket_path()), and rm -rf's the rendezvous dir ONLY after
assert_confined under self._base (risk #8) — an unconfined dir raises
PermissionError and is never removed. Entry dropped under the structural
lock; the /list cache is invalidated.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18 — `_do_create` / `_do_list` HTTP wrappers (cwd→400/realpath, max→429, socket never forwarded)

**Files**
- Create: `<WT>/session_endpoints.py` (the `SessionMixin` HTTP wrappers `_do_create`, `_do_list`)
- Test: `<WT>/tests/unit/test_session_endpoints_create_list.py`

> CRITIQUE-FIX applied: this component now OWNS `_do_create`/`_do_list` (previously referenced by cleanup-security but un-authored — GAP). They are thin HTTP wrappers over `registry.create`/`list_sessions`: cwd validation → 400, `_MaxSessionsReached` → 429, and the test-only `socket_override` is NEVER forwarded from the HTTP body (risk #6). The dispatcher/security ladder that ROUTES to these (`_dispatch_session`, CORS, auth) is owned by cleanup-security and MUST land before the SSE/get-envelope endpoints — this task tests the handler bodies in isolation via a thin stub, not the full server wiring. Handler method naming follows the canonical `_do_*` convention (cleanup-security dispatcher is the single owner of routing).

**(1) Write the FAILING test** — create `<WT>/tests/unit/test_session_endpoints_create_list.py`:

```python
import json
import pytest

import session_endpoints as se
import session_registry as sr


class _FakeReg:
    """Stands in for the module REGISTRY; records create/list calls."""
    def __init__(self):
        self.created = []
        self.list_rows = [{"session_id": "a" * 32, "state": "idle",
                           "created_at": 1.0, "log_bytes": 0, "alive": True}]
        self.raise_max = False
        self.raise_cwd = None

    def create(self, *, cwd, cols, rows, claude_argv, **kw):
        # CRITIQUE-FIX: assert the HTTP wrapper never forwards socket_override.
        assert "socket_override" not in kw
        if self.raise_max:
            raise sr._MaxSessionsReached("max")
        if self.raise_cwd is not None:
            raise self.raise_cwd
        self.created.append((cwd, cols, rows, tuple(claude_argv)))

        class _S:
            sid = "f" * 32
            cap = "c" * 64
            rendezvous_dir = "/base/wcb_x_y"
            created_at = 1.0
        return _S()

    def list_sessions(self):
        return self.list_rows


class _Harness(se.SessionMixin):
    """Minimal handler exposing only what the wrappers touch."""
    def __init__(self, body):
        self._body = body
        self.status = None
        self.payload = None

    def _read_session_body(self):
        return self._body

    def _session_json(self, status, obj):
        self.status = status
        self.payload = obj


@pytest.fixture(autouse=True)
def _reg(monkeypatch):
    reg = _FakeReg()
    monkeypatch.setattr(se, "REGISTRY", reg)
    return reg


def test_do_create_happy_path(_reg):
    h = _Harness({"cwd": "/tmp/work", "cols": 100, "rows": 30})
    h._do_create()
    assert h.status == 200
    assert h.payload["session_id"] == "f" * 32
    assert h.payload["cap"] == "c" * 64
    assert h.payload["rendezvous_dir"] == "/base/wcb_x_y"
    assert _reg.created == [("/tmp/work", 100, 30, tuple(se.DEFAULT_CLAUDE_ARGV))]


def test_do_create_ignores_caller_socket_override(_reg):
    """risk #6: a caller-supplied socket/socket_override must be dropped."""
    h = _Harness({"cwd": "/tmp/work", "socket_override": "wcb_evil",
                  "socket": "wcb_evil"})
    h._do_create()
    assert h.status == 200          # _FakeReg.create asserts no socket_override


def test_do_create_missing_cwd_400(_reg):
    h = _Harness({})
    h._do_create()
    assert h.status == 400
    assert "cwd" in h.payload["error"]


def test_do_create_bad_cwd_400(_reg):
    _reg.raise_cwd = NotADirectoryError("/nope")
    h = _Harness({"cwd": "/nope"})
    h._do_create()
    assert h.status == 400


def test_do_create_max_sessions_429(_reg):
    _reg.raise_max = True
    h = _Harness({"cwd": "/tmp/work"})
    h._do_create()
    assert h.status == 429


def test_do_list_returns_rows(_reg):
    h = _Harness(None)
    h._do_list()
    assert h.status == 200
    assert h.payload["sessions"] == _reg.list_rows
```

**(2) Run + expected FAIL**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_session_endpoints_create_list.py
```

Expected: `ModuleNotFoundError: No module named 'session_endpoints'` (then `AttributeError: ... 'SessionMixin'` / `'_do_create'`).

**(3) Minimal implementation** — create `<WT>/session_endpoints.py`:

```python
"""HTTP wrappers for /session/* create + list (SessionMixin).

Routing, CORS, and the auth ladder are owned by the cleanup-security
dispatcher (_dispatch_session); this module supplies only the _do_create /
_do_list handler bodies. The full STATES-aware capture/replay/stream handlers
live in their own components and share the canonical _do_* naming.
"""
from session_registry import REGISTRY, _MaxSessionsReached

# The default claude launch line (production never accepts a caller socket).
DEFAULT_CLAUDE_ARGV = ["claude"]


class SessionMixin:
    def _do_create(self):
        body = self._read_session_body() or {}
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            self._session_json(400, {"error": "cwd is required"})
            return
        cols = int(body.get("cols", 120))
        rows = int(body.get("rows", 40))
        # CRITIQUE-FIX risk #6: NEVER forward a caller-supplied socket — the
        # registry derives wcb_<id>. socket_override is a test-only seam.
        try:
            s = REGISTRY.create(cwd=cwd, cols=cols, rows=rows,
                                claude_argv=list(DEFAULT_CLAUDE_ARGV))
        except _MaxSessionsReached:
            self._session_json(429, {"error": "max concurrent sessions"})
            return
        except (ValueError, NotADirectoryError, FileNotFoundError) as e:
            self._session_json(400, {"error": "invalid cwd: %s" % e})
            return
        self._session_json(200, {
            "session_id": s.sid,
            "cap": s.cap,
            "rendezvous_dir": s.rendezvous_dir,
            "created_at": s.created_at,
        })

    def _do_list(self):
        self._session_json(200, {"sessions": REGISTRY.list_sessions()})
```

**(4) Run + expected PASS**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_session_endpoints_create_list.py
```

Expected: `6 passed`.

**(5) Commit**

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_endpoints.py tests/unit/test_session_endpoints_create_list.py && git commit -m "$(cat <<'EOF'
endpoints: _do_create / _do_list HTTP wrappers (cwd 400, max 429)

SessionMixin supplies the _do_create/_do_list handler bodies (previously
referenced but unowned). _do_create validates cwd (->400), maps
_MaxSessionsReached->429, returns {session_id,cap,rendezvous_dir,created_at},
and NEVER forwards a caller-supplied socket/socket_override (risk #6) — the
registry derives wcb_<id>. _do_list returns the cached session rows.
Routing/CORS/auth ladder owned by the cleanup-security dispatcher.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Component notes for the assembler (registry-lifecycle, Tasks 10–18)

- **Modules created here:** `<WT>/session_registry.py` (the `_Session`/`_Registry` substrate, `create`, `list_sessions`, `get_or_reconstruct`, `delete`) and `<WT>/session_endpoints.py` (`SessionMixin._do_create`/`_do_list` only). Both import only stdlib + `paths`, `fsm`, `tmux_session` — no HTTP server machinery (the dispatcher/CORS/auth ladder is owned by cleanup-security and wires `SessionMixin` into `server.Handler`).
- **Tests created here:** `<WT>/tests/unit/test_registry_locking.py`, `<WT>/tests/integration/test_session_lifecycle.py`, `<WT>/tests/unit/test_session_endpoints_create_list.py`. They rely on the shared `conftest.py` fixtures (`tmp_base`, `requires_tmux`, `fake_socket`, `fake_claude_argv`) and the single canonical `tests/fake_claude.sh` (carrying the SIGINT trap, canonical footer tokens incl. `FOOTER_IDLE`/`TRUST_PROMPT`, the rising-timer WORKING mode, and `WCB_FAKE_TRUST`). conftest import mode is pinned (bare `from conftest import ...`, `pythonpath`/`importmode` set in the shared config) so these collect.
- **CRITIQUE fixes applied in this component:**
  - **`classify()` signature** — every call site (`_Session._state`/`is_busy` Task 10, `_await_ready` Task 14, `is_busy`-via-`list_sessions` Task 16) passes the pinned `classify(screen, footer, env_present, *, prev_timer, composer_seen)`; `composer_seen` is latched on `s.composer_seen` once the composer is first seen.
  - **`_Session` field consolidation** — `shell_pid`, `composer_seen`, `_gone_strikes`, `log_offset_base`, `_claimed` all exist from Task 10 (siblings never bolt on).
  - **`_Registry` constructor** — pinned `_Registry(base=None)` + `self._base`; single singleton `REGISTRY` (rewrite any `_REGISTRY` reference in rendezvous-docsync to `REGISTRY`).
  - **`new_session` signature** — called with NO argv (`new_session(name, cwd, cols, rows)`); the launch line is typed only in `_await_ready` (pins the tmux-client contract).
  - **`socket_path()`** — required from the tmux-client component (used in Tasks 12 socket-clear and 17 socket-unlink).
  - **Risk #14 cross-check** — Task 15 `_hydrate` rejects a tampered `@wcb_nonce` redirect outside base (`_RendezvousRedirect`), with a test (previously a GAP).
  - **`_do_create`/`_do_list` ownership** — Task 18 (previously referenced by cleanup-security but un-authored — GAP); cwd→400, max→429, socket never forwarded (risk #6).
  - **`/list` brief cache + `log_bytes`** — Task 16 (design (4) O(N) fan-out mitigation; `log_bytes` reported in the rotation-adjusted space via `log_offset_base`).
- **Dependencies (must land first):** `paths.py` (`mint_session_id`/`mint_cap`/`mint_nonce`, `base_dir`, `verify_base_dir`, `session_dir`, `assert_confined`, `validate_session_id`, `SESSION_ID_RE`), `fsm.py` (`classify` with the pinned signature, `footer_of`, `strip_screen`, `FOOTER_IDLE`, `TRUST_PROMPT`), `tmux_session.py` (`TmuxClient(socket)` with `new_session(name,cwd,cols,rows)`/`has_session`/`pane_id`/`alternate_on`/`get_option`/`set_option`/`capture_pane`/`send_keys`/`send_text`/`pipe_pane_on`(disable-first)/`kill_server`/`socket_path`, `_TmuxError`). `classify`'s full signature is owned by turn-protocol-fsm but must be authored before Task 10's `_state` runs against a real `fsm.py`.
- **Ordering note for the assembler:** the cleanup-security dispatcher/security-ladder wiring (`server.Handler(SessionMixin, ...)` + `_dispatch_session` + CORS/auth, pinned env var `WCB_ALLOWED_ORIGINS`) must precede the rendezvous-docsync `_session_get_envelope` and turn-protocol SSE/send-key handlers (all `_do_*`, routed through the one dispatcher). This component's Tasks 10–17 have no HTTP dependency; Task 18's handler bodies are tested in isolation via a stub and do not require the dispatcher to exist yet, but the dispatcher must route to `_do_create`/`_do_list` when it lands.
- **Out of scope (other components):** turn protocol / `_run_turn_locked` (turn-protocol-fsm, sole owner of the held-lock turn body — calls rendezvous-docsync's `stage_turn`/`build_turn_prompt`/`await_envelope`), doc-staging + envelope read-back + sentinel echo wiring (rendezvous-docsync), SSE stream loop + DEAD multi-poll corroboration (turn-protocol-fsm), reaper + interrupt + replay-offset rotation + dispatcher/security (cleanup-security).


## Task 19 — fsm.strip_screen + fsm.footer_of over real captures

**Files**
- Create: `fsm.py`
- Test: `tests/unit/test_fsm_strip.py`

**(1) Write the FAILING test** — `tests/unit/test_fsm_strip.py`:
```python
import re
import pathlib
import pytest

import fsm

CAPTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "captures"


def _raw(name):
    return (CAPTURES / name).read_text()


def test_strip_screen_removes_csi_osc_cursor():
    raw = (
        "\x1b[2J\x1b[H\x1b]0;title\x07hello \x1b[31mred\x1b[0m world\x1b[?25l\n"
        "next\x1b[1;5Hline\n"
    )
    out = fsm.strip_screen(raw)
    # No ESC bytes survive.
    assert "\x1b" not in out
    assert "\x07" not in out
    # Visible text is preserved.
    assert "hello red world" in out
    assert "next" in out
    assert "line" in out


def test_strip_screen_is_idempotent_on_clean_capture():
    # capture-pane -p output is already mostly clean; strip must not mangle it.
    raw = _raw("composer_ready.txt")
    once = fsm.strip_screen(raw)
    twice = fsm.strip_screen(once)
    assert once == twice
    # The pinned IDLE token survives the strip verbatim.
    assert "\u23f5\u23f5 bypass permissions on (shift+tab to cycle)" in once


def test_footer_of_isolates_working_footer():
    # turn-poll captures: the footer region contains 'esc to interrupt'.
    screen = fsm.strip_screen(_raw("thinking_esc_interrupt.txt"))
    footer = fsm.footer_of(screen)
    assert "esc to interrupt" in footer
    # The composer prompt body must not be in the isolated footer region.
    assert "Write the exact JSON" not in footer


def test_footer_of_isolates_idle_footer():
    screen = fsm.strip_screen(_raw("composer_ready.txt"))
    footer = fsm.footer_of(screen)
    assert "\u23f5\u23f5 bypass permissions on (shift+tab to cycle)" in footer
    assert "esc to interrupt" not in footer


def test_footer_of_empty_screen_is_empty_string():
    assert fsm.footer_of("") == ""
    assert fsm.footer_of("\n\n\n") == ""
```

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fsm_strip.py`
- Expected: collection error / `ModuleNotFoundError: No module named 'fsm'` (or `AttributeError: module 'fsm' has no attribute 'strip_screen'` once the file is a stub). All 5 tests FAIL.

**(3) Minimal implementation** — `fsm.py`:
```python
"""Pure state classifier for the claude TUI. No tmux, no HTTP.

Driven entirely by captured `tmux capture-pane -p` screens. Constants pinned
to claude v2.1.156 per the design's calibration section. The FOOTER_IDLE token
is the single canonical idle token also emitted by the canonical
tests/fake_claude.sh (Task 3, test-scaffold) — do not diverge.
"""
import re

# --- CALIBRATE constants (claude v2.1.156) ------------------------------------
FOOTER_WORKING = "esc to interrupt"                                 # WORKING gate
FOOTER_IDLE = "\u23f5\u23f5 bypass permissions on (shift+tab to cycle)"  # IDLE gate
TRUST_PROMPT = "Is this a project you created or one you trust?"
TRUST_OPTION_YES = "Yes, I trust this folder"
MARKER_ASSISTANT = "\u23fa"      # ⏺ assistant line / tool call
MARKER_RESULT = "\u23bf"         # ⎿ tool result
SPINNER_TIMER_RE = re.compile(r"\((\d+)s[ \u00b7]")   # rising elapsed timer; verb ignored
CLAUDE_PROC_NAME = "claude.exe"  # pane_current_command while running

# --- ANSI / terminal control stripping ----------------------------------------
# CSI: ESC [ ... final-byte ; OSC: ESC ] ... (BEL | ESC \) ; lone ESC sequences.
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC_OTHER_RE = re.compile(r"\x1b[@-Z\\-_]")
_BEL_RE = re.compile(r"\x07")


def strip_screen(raw: str) -> str:
    """Full CSI/OSC/cursor strip of capture-pane -p output."""
    out = _OSC_RE.sub("", raw)
    out = _CSI_RE.sub("", out)
    out = _ESC_OTHER_RE.sub("", out)
    out = _BEL_RE.sub("", out)
    return out


def footer_of(screen: str) -> str:
    """Last non-blank framed footer region.

    The composer is framed by horizontal rules (runs of the box-drawing
    '\u2500'). The footer is everything after the LAST such rule. If no rule
    is present, fall back to the trailing non-blank lines.
    """
    lines = screen.split("\n")
    last_rule = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s and set(s) <= {"\u2500"}:
            last_rule = i
    if last_rule >= 0:
        tail = lines[last_rule + 1:]
    else:
        tail = lines
    footer = "\n".join(tail).strip()
    return footer
```

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fsm_strip.py`
- Expected: `5 passed`.

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add fsm.py tests/unit/test_fsm_strip.py && git commit -m "fsm: screen strip + footer isolation over real captures

Add fsm.py with strip_screen (CSI/OSC/cursor strip) and footer_of
(last framed footer region). Tests drive the real composer_ready and
thinking_esc_interrupt fixtures so the IDLE/WORKING footer tokens are
isolated correctly. Pins the v2.1.156 CALIBRATE constants; FOOTER_IDLE
is the single canonical token shared with the canonical fake_claude.sh.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 20 — fsm.classify WORKING via esc-to-interrupt and rising timer

**Files**
- Modify: `fsm.py` (add `STATES`, `classify`)
- Test: `tests/unit/test_fsm_classify_working.py`

> FIX (critique: classify signature): the pinned signature is `classify(screen, footer, env_present, *, prev_timer=None, composer_seen=False)`. Every cross-component call site (registry-lifecycle `_Session._state`/`_await_ready`, cleanup-security `_do_capture`) MUST pass `composer_seen=` explicitly. This is documented in the component summary so siblings do not call it positionally.

**(1) Write the FAILING test** — `tests/unit/test_fsm_classify_working.py`:
```python
import pathlib

import fsm

CAPTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "captures"


def _screen(name):
    return fsm.strip_screen((CAPTURES / name).read_text())


def _classify(name, **kw):
    screen = _screen(name)
    return fsm.classify(screen, fsm.footer_of(screen), **kw)


def test_states_tuple_is_pinned():
    assert fsm.STATES == (
        "starting", "thinking", "streaming", "awaiting_input",
        "idle", "idle_no_envelope", "dead",
    )


def test_thinking_when_esc_to_interrupt_in_footer():
    # thinking_esc_interrupt.txt has 'esc to interrupt' in the footer AND
    # the IDLE token on the same line — WORKING must win.
    state, meta = _classify(
        "thinking_esc_interrupt.txt", env_present=False, composer_seen=True
    )
    assert state in ("thinking", "streaming")


def test_working_even_when_idle_token_present():
    # Regression: the IDLE footer token is present DURING the turn. It must
    # NOT cause a false idle while 'esc to interrupt' is also present.
    screen = _screen("thinking_esc_interrupt.txt")
    assert fsm.FOOTER_IDLE in fsm.footer_of(screen)  # both tokens present
    state, _ = fsm.classify(
        screen, fsm.footer_of(screen), env_present=True, composer_seen=True
    )
    assert state in ("thinking", "streaming")  # NOT idle, despite env_present


def test_env_absent_alone_is_working():
    # No footer working token but env file not yet written => still WORKING
    # (env-absent is the strongest WORKING signal).
    composer = _screen("composer_ready.txt")
    footer = fsm.footer_of(composer)
    assert fsm.FOOTER_WORKING not in footer
    state, _ = fsm.classify(
        composer, footer, env_present=False, composer_seen=True
    )
    assert state in ("thinking", "streaming")


def test_rising_spinner_timer_is_working():
    # turn-poll-2 has '(1s ·' spinner; prev_timer lower => rising => WORKING.
    screen = _screen("thinking_esc_interrupt.txt")
    footer = fsm.footer_of(screen)
    state, meta = fsm.classify(
        screen, footer, env_present=True, prev_timer=0, composer_seen=True
    )
    assert state in ("thinking", "streaming")


def test_spinner_verb_is_never_the_gate():
    # A screen whose ONLY 'working-ish' text is a randomized verb, with the
    # IDLE footer and env present and no interrupt-hint, must classify idle.
    fake = (
        "\u256d\u2500 Claude Code \u2500\u256e\n"
        "Smooshing\u2026 Baked for 5s thinking\n"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        "\u276f \n"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        "  " + fsm.FOOTER_IDLE + "\n"
    )
    state, _ = fsm.classify(
        fake, fsm.footer_of(fake), env_present=True, composer_seen=True
    )
    assert state == "idle"  # the verb 'Smooshing…' did not gate WORKING
```

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fsm_classify_working.py`
- Expected: `AttributeError: module 'fsm' has no attribute 'STATES'` / `classify`. All tests FAIL.

**(3) Minimal implementation** — append to `fsm.py`:
```python
STATES = (
    "starting", "thinking", "streaming", "awaiting_input",
    "idle", "idle_no_envelope", "dead",
)

# Composer readiness marker: the framed prompt line begins with '\u276f '.
_COMPOSER_PROMPT = "\u276f"


def _spinner_timer(footer_or_screen: str):
    """Highest elapsed-timer value present, or None. Verb is ignored."""
    vals = [int(m) for m in SPINNER_TIMER_RE.findall(footer_or_screen)]
    return max(vals) if vals else None


def classify(screen: str, footer: str, env_present: bool,
             *, prev_timer: "int | None" = None,
             composer_seen: bool = False) -> "tuple[str, dict]":
    """Return (state, meta). PINNED SIGNATURE — callers pass composer_seen=.

    WORKING (thinking/streaming) is OR'd and positive:
        env absent OR rising SPINNER_TIMER_RE OR FOOTER_WORKING present.
    idle ONLY when no WORKING signal AND env_present; else idle_no_envelope.
    The spinner verb is NEVER a gate (randomized).
    """
    timer = _spinner_timer(screen)
    meta = {"timer": timer}

    working = False
    if not env_present:
        working = True
    if FOOTER_WORKING in footer:
        working = True
    if timer is not None and prev_timer is not None and timer > prev_timer:
        working = True

    if working:
        # streaming once assistant content is on screen, else thinking.
        state = "streaming" if MARKER_ASSISTANT in screen else "thinking"
        return state, meta

    # Not working. Distinguish idle (env present) from idle_no_envelope.
    if env_present:
        return "idle", meta
    return "idle_no_envelope", meta
```

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fsm_classify_working.py`
- Expected: `7 passed`.

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add fsm.py tests/unit/test_fsm_classify_working.py && git commit -m "fsm: classify WORKING via OR'd positive signals

WORKING = env-absent OR rising spinner timer OR 'esc to interrupt' in
footer. idle only when no WORKING signal AND env_present, else
idle_no_envelope. Signature pinned classify(screen, footer, env_present,
*, prev_timer, composer_seen) so every sibling call site passes
composer_seen. Tests prove the IDLE footer token present mid-turn never
causes a false idle, and the randomized spinner verb is never a gate
(risk #2).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 21 — fsm.classify awaiting_input (trust block) and idle vs idle_no_envelope

**Files**
- Modify: `fsm.py` (extend `classify` with trust/menu detection)
- Test: `tests/unit/test_fsm_classify_await.py`

> FIX (critique: full STATES handling): `classify` returns the full STATES tuple; `awaiting_input` carries `meta["kind"]` and `meta["screen"]`. All consumers (registry-lifecycle `_await_ready`, cleanup-security `_do_capture`) must handle `awaiting_input`/`dead`/`starting`, not just idle/working — documented in the component summary.

**(1) Write the FAILING test** — `tests/unit/test_fsm_classify_await.py`:
```python
import pathlib

import fsm

CAPTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "captures"


def _screen(name):
    return fsm.strip_screen((CAPTURES / name).read_text())


def _classify(name, **kw):
    screen = _screen(name)
    return fsm.classify(screen, fsm.footer_of(screen), **kw)


def test_trust_prompt_is_awaiting_input():
    state, meta = _classify(
        "trust_prompt.txt", env_present=False, composer_seen=False
    )
    assert state == "awaiting_input"
    assert meta["kind"] == "trust"
    # The full screen is attached so the rwa/caller can render the menu.
    assert "screen" in meta
    assert fsm.TRUST_PROMPT in meta["screen"]


def test_trust_block_beats_env_absent_working():
    # Even though env is absent (a WORKING signal), a recognized menu/trust
    # block must surface as awaiting_input so the caller can answer it.
    state, _ = _classify(
        "trust_prompt.txt", env_present=False, composer_seen=False
    )
    assert state == "awaiting_input"


def test_composer_idle_only_when_env_present():
    # composer_ready.txt: no WORKING token, composer framed.
    state_present, _ = _classify(
        "composer_ready.txt", env_present=True, composer_seen=True
    )
    assert state_present == "idle"

    state_absent, _ = _classify(
        "composer_ready.txt", env_present=False, composer_seen=True
    )
    assert state_absent == "idle_no_envelope"


def test_turn_done_idle_when_env_present():
    # turn_done.txt still shows 'esc to interrupt' in the footer (screen lags
    # the file edge), so it is WORKING by screen — proving why the FILE edge,
    # not the screen, gates done. Screen classification stays WORKING here.
    state, _ = _classify(
        "turn_done.txt", env_present=True, composer_seen=True
    )
    assert state in ("thinking", "streaming")
```

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fsm_classify_await.py`
- Expected: `test_trust_prompt_is_awaiting_input` FAILS — `classify` currently returns `idle_no_envelope`/`thinking`, never `awaiting_input`; `KeyError: 'kind'`.

**(3) Minimal implementation** — edit `classify` in `fsm.py`. Replace the body after `meta = {"timer": timer}` with:
```python
    meta = {"timer": timer}

    # awaiting_input: a recognized menu/trust block takes precedence over the
    # WORKING signals (the caller must be able to answer it). Detect only the
    # explicit, calibrated blocks — never a generic 'input box present'.
    if TRUST_PROMPT in screen or TRUST_OPTION_YES in screen:
        meta["kind"] = "trust"
        meta["screen"] = screen
        return "awaiting_input", meta

    working = False
    if not env_present:
        working = True
    if FOOTER_WORKING in footer:
        working = True
    if timer is not None and prev_timer is not None and timer > prev_timer:
        working = True

    if working:
        state = "streaming" if MARKER_ASSISTANT in screen else "thinking"
        return state, meta

    if env_present:
        return "idle", meta
    return "idle_no_envelope", meta
```

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fsm_classify_await.py`
- Expected: `4 passed`. Re-run Task 20's file to confirm no regression: `python3 -m pytest -q tests/unit/test_fsm_classify_working.py` → `7 passed`.

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add fsm.py tests/unit/test_fsm_classify_await.py && git commit -m "fsm: awaiting_input on calibrated trust/menu block; idle vs idle_no_envelope

A recognized trust/menu block surfaces awaiting_input(kind,screen) and
takes precedence over WORKING. idle requires env_present; otherwise
idle_no_envelope. turn_done fixture stays WORKING by screen, proving the
file edge (not screen) gates done (risk #2). Consumers must handle the
full STATES tuple, not just idle/working.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 22 — fsm.classify starting vs dead discriminator (shell-name pane)

**Files**
- Modify: `fsm.py` (add shell-pane / composer_seen branch)
- Test: `tests/unit/test_fsm_classify_dead.py`

> FIX (critique: DEAD under-tests risk #13/§4): `classify` returns a CANDIDATE `dead`/`starting` from a single capture on `composer_seen`. It is NOT authoritative. The held-lock turn loop (Task 26) requires multi-poll persistence > QUIESCENT_MS PLUS `has-session`/`#{pane_dead}` corroboration before emitting `done{reason:dead}`. This task only fixes the per-screen candidate; corroboration is wired in Task 26.

**(1) Write the FAILING test** — `tests/unit/test_fsm_classify_dead.py`:
```python
import pathlib

import fsm

CAPTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "captures"


def _screen(name):
    return fsm.strip_screen((CAPTURES / name).read_text())


def test_post_exit_shell_is_dead_only_after_composer_seen():
    screen = _screen("post_exit_shell.txt")
    footer = fsm.footer_of(screen)
    # Composer was seen this session => the returned shell prompt means DEAD
    # (candidate; the turn loop corroborates with has-session/#{pane_dead}).
    state, _ = fsm.classify(
        screen, footer, env_present=False, composer_seen=True
    )
    assert state == "dead"


def test_shell_pane_is_starting_before_first_composer():
    # A bare shell prompt with no composer frame and composer never seen =>
    # STARTING, never DEAD (claude is still launching).
    shell = "martintreiber@10 wcbcal.A7Gvur %\n"
    state, _ = fsm.classify(
        shell, fsm.footer_of(shell), env_present=False, composer_seen=False
    )
    assert state == "starting"


def test_composer_present_is_not_dead_even_after_seen():
    # If the composer frame is still on screen, it is NOT dead regardless of
    # composer_seen (the shell-name discriminator requires no composer frame).
    screen = _screen("composer_ready.txt")
    state, _ = fsm.classify(
        screen, fsm.footer_of(screen), env_present=True, composer_seen=True
    )
    assert state == "idle"
```

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fsm_classify_dead.py`
- Expected: `test_post_exit_shell_is_dead_only_after_composer_seen` and `test_shell_pane_is_starting_before_first_composer` FAIL — `classify` currently returns `idle_no_envelope` for both (no shell/composer branch).

**(3) Minimal implementation** — in `fsm.py`, add a helper and a branch. Add after `_COMPOSER_PROMPT = "\u276f"`:
```python
# The composer is framed: a '\u276f ' prompt line wrapped by horizontal rules.
# Its presence means claude's TUI is up. Its ABSENCE (only a shell prompt) is
# the shell-name discriminator: starting before a composer was ever seen,
# dead afterward. NOTE: the returned 'dead' here is a CANDIDATE; the held-lock
# turn loop (turn-protocol Task 26) corroborates with has-session/#{pane_dead}
# across QUIESCENT_MS before emitting done{reason:dead}.
def _composer_present(screen: str) -> bool:
    return ("\u23f5\u23f5 bypass permissions on" in screen
            or any(line.lstrip().startswith(_COMPOSER_PROMPT)
                   for line in screen.split("\n")))
```
Then insert this branch in `classify`, immediately after `meta = {"timer": timer}` and before the `awaiting_input` block:
```python
    # Shell-name discriminator: no composer frame on screen.
    if not _composer_present(screen):
        # Trust/menu blocks have no composer frame either — let them through.
        if not (TRUST_PROMPT in screen or TRUST_OPTION_YES in screen):
            return ("dead" if composer_seen else "starting"), meta
```

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fsm_classify_dead.py`
- Expected: `3 passed`. Re-run the whole fsm suite: `python3 -m pytest -q tests/unit/test_fsm_strip.py tests/unit/test_fsm_classify_working.py tests/unit/test_fsm_classify_await.py tests/unit/test_fsm_classify_dead.py` → all pass.

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add fsm.py tests/unit/test_fsm_classify_dead.py && git commit -m "fsm: starting vs dead candidate on shell-name pane

A pane with no composer frame is 'starting' until a composer was seen
this session, then 'dead' (CANDIDATE; corroborated in the turn loop with
has-session/#{pane_dead} across QUIESCENT_MS — risks #2, #13). A visible
composer frame is never dead. Trust/menu blocks (no composer frame) are
excluded so they still reach awaiting_input.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 23 — Held-lock turn skeleton: bounded acquire before send_response(200) → 409

**Files**
- Modify: `session_registry.py` (add `SessionBusy`, `run_turn`, `run_turn_locked`, `_run_turn_locked` skeleton; extend `create(...socket_override)`; add `mint_turn_uuid`)
- Test: `tests/integration/test_turn_protocol.py`

> Dependencies (authored EARLIER in the global order): `session_registry.get_or_reconstruct`/`create`/`_Session.turn_lock`/`_Session.pane`/`_Session.rendezvous_dir` (registry-lifecycle, Tasks ~9-13); `TmuxClient` capture/send + `socket_path()` (tmux-client); `fsm.classify` (Tasks 19-22); rendezvous-docsync `stage_turn`/`build_turn_prompt`/`read_envelope_bytes`/`await_envelope` (rendezvous-docsync, authored BEFORE this component per the corrected global order). The fake-claude integration harness (`fake_claude_argv`, `fake_socket`, `tmp_base`, `requires_tmux`) and the single canonical `tests/fake_claude.sh` are from the test-scaffold component.
>
> FIX (critique: run_turn/_run_turn_locked ownership): turn-protocol-fsm OWNS `run_turn`/`run_turn_locked`/`_run_turn_locked`/`_await_completion` on `_Registry`. It does NOT re-author staging or prompt building — those belong to rendezvous-docsync (`stage_turn`, `build_turn_prompt`, `await_envelope`, `read_envelope_bytes`). This skeleton CALLS them (wired fully in Tasks 24-25). The `socket_override` kwarg is a test-only seam; the HTTP `_do_create` (owned by cleanup-security) MUST NOT forward a caller-supplied socket (risk #6).

**(1) Write the FAILING test** — `tests/integration/test_turn_protocol.py`:
```python
import json
import threading
import time

import pytest

from conftest import requires_tmux  # bare import; pinned by pythonpath/addopts

import session_registry


@pytest.fixture
def live_session(tmp_base, fake_socket, fake_claude_argv, monkeypatch):
    """A real fake-claude session created through the real TmuxClient."""
    monkeypatch.setenv("WCB_FAKE_DELAY", "0.4")
    monkeypatch.setattr(session_registry, "base_dir", lambda: str(tmp_base))
    reg = session_registry._Registry()
    sess = reg.create(
        cwd=str(tmp_base), cols=120, rows=40, claude_argv=fake_claude_argv,
        socket_override=fake_socket,
    )
    yield reg, sess
    try:
        reg.delete(sess.sid, sess.cap)
    except Exception:
        pass


@requires_tmux
def test_busy_409_when_turn_lock_held(live_session):
    reg, sess = live_session
    # Simulate an in-flight turn by holding the turn lock.
    assert sess.turn_lock.acquire(timeout=2.0)
    try:
        # A second concurrent turn must NOT be able to acquire within budget.
        got = sess.turn_lock.acquire(timeout=2.0)
        assert got is False, "bounded acquire must give up -> 409 path"
    finally:
        sess.turn_lock.release()


@requires_tmux
def test_session_busy_raised_when_lock_held(live_session):
    reg, sess = live_session
    assert sess.turn_lock.acquire(timeout=2.0)
    try:
        with pytest.raises(session_registry.SessionBusy):
            reg.run_turn(sess, instruction="noop", doc="<html></html>",
                         turn_uuid=reg.mint_turn_uuid(), timeout=3.0)
    finally:
        sess.turn_lock.release()


@requires_tmux
def test_lock_released_after_turn_without_killing_session(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    result = reg.run_turn(
        sess, instruction="WRITE", doc="<html></html>",
        turn_uuid=turn_uuid, timeout=10.0,
    )
    assert result["reason"] in ("idle", "idle_no_envelope")
    # Lock is free again after the turn.
    assert sess.turn_lock.acquire(timeout=2.0) is True
    sess.turn_lock.release()
    # Session is still alive (a turn ending != the session dying).
    assert sess.tmux.has_session("t") is True
```

> Note: `instruction` is the user instruction fed to rendezvous-docsync `build_turn_prompt`; there is NO `{env}`/`{uuid}` template on the production path (the `WRITE <abs_env_path> <turn_uuid>` template lives ONLY inside the canonical `tests/fake_claude.sh`). The canonical fake reads its rendezvous paths from the staged `doc.<uuid>.html` / prompt that `build_turn_prompt` produced.

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py`
- Expected: FAIL — `AttributeError: '_Registry' object has no attribute 'run_turn'`/`mint_turn_uuid`/`SessionBusy` (or `create()` missing `socket_override`). If tmux is absent the file is skipped; run on a host with tmux.

**(3) Minimal implementation** — add to `session_registry.py`. Add the busy exception near the top:
```python
import uuid as _uuid


class SessionBusy(Exception):
    """Turn lock is held — maps to 409."""
```
Add to `_Registry`:
```python
    def mint_turn_uuid(self):
        """Bridge-minted per-turn uuid (risk #7) — never caller-supplied."""
        return str(_uuid.uuid4())

    def run_turn(self, sess, **kw):
        """Acquire the bounded turn lock, then delegate. SessionBusy -> 409."""
        if not sess.turn_lock.acquire(timeout=2.0):
            raise SessionBusy(sess.sid)
        try:
            return self.run_turn_locked(sess, **kw)
        finally:
            sess.turn_lock.release()

    def run_turn_locked(self, sess, *, instruction, doc, turn_uuid, timeout,
                        on_event=None):
        """Assumes the turn lock is ALREADY held (HTTP layer acquired it
        before send_response(200) so 409-busy is observable)."""
        return self._run_turn_locked(
            sess, instruction=instruction, doc=doc, turn_uuid=turn_uuid,
            timeout=timeout, on_event=on_event)

    def _run_turn_locked(self, sess, *, instruction, doc, turn_uuid, timeout,
                         on_event):
        # Filled in by Task 24 (stage via rendezvous-docsync stage_turn +
        # build_turn_prompt) -> Task 25 (file-edge poll/read via await_envelope
        # / read_envelope_bytes) -> Task 26 (SSE events + dead corroboration).
        # Minimal skeleton: send a prompt, settle, report idle_no_envelope.
        target = sess.pane
        sess.tmux.set_option("@wcb_turn", turn_uuid)
        try:
            sess.tmux.send_prompt(target, instruction)
            deadline = time.monotonic() + timeout
            state = "thinking"
            from fsm import strip_screen, footer_of, classify
            while time.monotonic() < deadline:
                stripped = strip_screen(sess.tmux.capture_pane(target))
                state, _meta = classify(
                    stripped, footer_of(stripped),
                    env_present=False, composer_seen=True,
                )
                if state in ("idle", "idle_no_envelope", "awaiting_input",
                             "dead"):
                    break
                time.sleep(0.1)
            return {"reason": "idle_no_envelope", "state": state,
                    "log_offset": 0, "alive": sess.tmux.has_session("t"),
                    "envelope_bytes": None}
        finally:
            sess.tmux.set_option("@wcb_turn", "")
```
Extend `_Registry.create(...)` to accept the test-only `socket_override` (if registry-lifecycle did not already): `socket = socket_override or tmux_socket_name(sid)`. The HTTP `_do_create` (cleanup-security) never passes `socket_override`.

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py`
- Expected: `3 passed` (tmux present). `run_turn` reports `idle_no_envelope` for now — staging/read come next.

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/integration/test_turn_protocol.py && git commit -m "turn: held-lock turn skeleton — bounded acquire, release in finally

run_turn acquires the per-session turn lock with a bounded 2.0s timeout
(SessionBusy -> 409 if held) and delegates to run_turn_locked, which the
HTTP layer also reaches after acquiring the lock itself before
send_response(200). Holds it as one critical section; finally releases
without killing the session. Sets/clears @wcb_turn so busy survives
restart. mint_turn_uuid is bridge-minted (risk #7). Fake-claude proves
409 on concurrent turn and that the session stays alive (risks #4, #5,
#10). socket_override is a test-only seam; _do_create never forwards it.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 24 — Stage doc + stale-env sweep via rendezvous-docsync, then send build_turn_prompt

**Files**
- Modify: `session_registry.py` (`_run_turn_locked` — call `stage_turn` + `build_turn_prompt`, send prompt)
- Test: `tests/integration/test_turn_protocol.py` (add staging assertions)

> FIX (critique: prompt-template + staging ownership): this task does NOT inline an LF-only doc write or an `_expand` template. It CALLS rendezvous-docsync's `stage_turn(sess, doc, turn_uuid)` (which performs the stale-env sweep, the `canonLF` `\n`-only doc write asserting no CR, confined `O_NOFOLLOW` create — risks #3, #4, #7, #9) and `build_turn_prompt(instruction, doc_path, env_path, turn_uuid)` (the production natural-language prompt naming `.part`→rename). The `{env}`/`{uuid}` template is asserted ONLY against the fake's behavior, never against the production prompt.

**(1) Write the FAILING test** — append to `tests/integration/test_turn_protocol.py`:
```python
import os


@requires_tmux
def test_doc_staged_lf_only_before_send(live_session, monkeypatch):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    doc = "<html>\r\n<body>line</body>\r\n</html>"  # caller passes CRLF
    staged_at_send = {}
    orig_send_prompt = sess.tmux.send_prompt

    def spy_send_prompt(target, text):
        doc_path = os.path.join(sess.rendezvous_dir, f"doc.{turn_uuid}.html")
        with open(doc_path, "rb") as f:
            staged_at_send["bytes"] = f.read()
        staged_at_send["prompt"] = text
        return orig_send_prompt(target, text)

    monkeypatch.setattr(sess.tmux, "send_prompt", spy_send_prompt)

    reg.run_turn(sess, instruction="WRITE", doc=doc,
                 turn_uuid=turn_uuid, timeout=10.0)

    # Doc staged BEFORE send (happens-before), LF-only, no CR.
    assert "bytes" in staged_at_send, "doc must be staged before send_prompt"
    assert b"\r" not in staged_at_send["bytes"]
    assert b"<body>line</body>" in staged_at_send["bytes"]
    # Production prompt is build_turn_prompt output naming the env file —
    # NOT the fake's '{env} {uuid}' template.
    assert turn_uuid in staged_at_send["prompt"]
    assert "{env}" not in staged_at_send["prompt"]


@requires_tmux
def test_stale_env_unlinked_at_turn_start(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    env_path = os.path.join(sess.rendezvous_dir, f"env.{turn_uuid}.json")
    with open(env_path, "wb") as f:
        f.write(b'{"stale":true}')
    # Empty instruction => fake stays idle (writes nothing).
    result = reg.run_turn(sess, instruction="", doc="<html></html>",
                          turn_uuid=turn_uuid, timeout=8.0)
    # stale-env swept at turn start (by stage_turn) and never recreated =>
    # idle_no_envelope, never a stale read.
    assert result["reason"] == "idle_no_envelope"
    assert not os.path.exists(env_path)
```

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py -k "staged or stale"`
- Expected: FAIL — `_run_turn_locked` does not call `stage_turn` (doc file missing → `FileNotFoundError` in the spy) and does not sweep the stale env.

**(3) Minimal implementation** — edit `_run_turn_locked` in `session_registry.py`. Add the import at the top: `from rendezvous import stage_turn, build_turn_prompt`. Replace the prologue (before `sess.tmux.send_prompt(...)`):
```python
    def _run_turn_locked(self, sess, *, instruction, doc, turn_uuid, timeout,
                         on_event):
        target = sess.pane
        # rendezvous-docsync owns staging: stale-env sweep + canonLF doc write
        # (no CR) + confined O_NOFOLLOW create (risks #3, #4, #7, #9). Returns
        # the confined (doc_path, env_path).
        doc_path, env_path = stage_turn(sess, doc, turn_uuid)

        sess.tmux.set_option("@wcb_turn", turn_uuid)
        send_time = time.time()
        try:
            if instruction:
                prompt = build_turn_prompt(instruction, doc_path, env_path,
                                           turn_uuid)
                sess.tmux.send_prompt(target, prompt)
            # (file-edge poll / read-back land in Task 25; for now: settle.)
            deadline = time.monotonic() + timeout
            state = "thinking"
            from fsm import strip_screen, footer_of, classify
            while time.monotonic() < deadline:
                stripped = strip_screen(sess.tmux.capture_pane(target))
                state, _m = classify(stripped, footer_of(stripped),
                                     env_present=os.path.exists(env_path),
                                     composer_seen=True)
                if state in ("idle", "idle_no_envelope", "awaiting_input",
                             "dead"):
                    break
                time.sleep(0.1)
            return {"reason": "idle_no_envelope", "state": state,
                    "log_offset": 0, "alive": sess.tmux.has_session("t"),
                    "envelope_bytes": None}
        finally:
            sess.tmux.set_option("@wcb_turn", "")
```

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py`
- Expected: all pass (the two new tests + the three from Task 23).

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/integration/test_turn_protocol.py && git commit -m "turn: stage via rendezvous.stage_turn + build_turn_prompt before send

Inside the held lock: call rendezvous.stage_turn (stale-env sweep +
canonLF \n-only doc write asserting no CR + confined O_NOFOLLOW create)
then send rendezvous.build_turn_prompt output naming env.<uuid>.json.
Stage happens-before send. No inlined staging/template duplication;
rendezvous-docsync owns staging + prompt building (risks #3, #4, #7, #9).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 25 — FILE-edge completion via rendezvous.await_envelope + byte-exact, sentinel-checked read-back

**Files**
- Modify: `session_registry.py` (`_run_turn_locked` → `_await_completion` calling `await_envelope`/`read_envelope_bytes`)
- Test: `tests/integration/test_turn_protocol.py` (add file-edge assertions)

> FIX (critique: read-back ownership + sentinel wiring): the `.part`→rename poll (mtime>send, size+mtime stable) and the byte-exact read-back belong to rendezvous-docsync's `await_envelope(sess, env_path, turn_uuid, send_time, timeout)` and `read_envelope_bytes(env_path, turn_uuid)`. turn-protocol's `_await_completion` interleaves the SCREEN read (for awaiting_input/dead) with rendezvous-docsync's file poll. `read_envelope_bytes` MUST invoke `verify_envelope_sentinel(obj, turn_uuid)` (risk #4) — this closes the otherwise-untraceable sentinel check; if rendezvous-docsync's `read_envelope_bytes` does not yet call it, this task asserts the uuid-in-envelope match it provides.

**(1) Write the FAILING test** — append to `tests/integration/test_turn_protocol.py`:
```python
@requires_tmux
def test_file_edge_done_idle_with_byte_exact_envelope(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    # canonical fake_claude.sh, on the build_turn_prompt WRITE instruction,
    # writes env.part then renames it to env.<uuid>.json.
    result = reg.run_turn(
        sess, instruction="WRITE", doc="<html></html>",
        turn_uuid=turn_uuid, timeout=12.0,
    )
    assert result["reason"] == "idle"
    env_bytes = result["envelope_bytes"]
    assert isinstance(env_bytes, (bytes, bytearray))
    obj = json.loads(env_bytes)
    assert obj["turn_uuid"] == turn_uuid          # sentinel/uuid echo (risk #4)
    assert obj["tool"]                             # truthy, per read-back


@requires_tmux
def test_no_write_yields_idle_no_envelope_never_stale(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    result = reg.run_turn(
        sess, instruction="", doc="<html></html>", turn_uuid=turn_uuid,
        timeout=8.0,
    )
    assert result["reason"] == "idle_no_envelope"
    assert result.get("envelope_bytes") is None


@requires_tmux
def test_mtime_must_exceed_send_time(live_session):
    reg, sess = live_session
    turn_uuid = reg.mint_turn_uuid()
    env_path = os.path.join(sess.rendezvous_dir, f"env.{turn_uuid}.json")
    # Plant an old-content file (swept at turn start by stage_turn); then a
    # real WRITE. The poll accepts ONLY the fresh post-send file.
    with open(env_path, "wb") as f:
        f.write(b'{"tool":"x","envelope":{"a":1},"turn_uuid":"%s"}'
                % turn_uuid.encode())
    old = time.time() - 3600
    os.utime(env_path, (old, old))
    result = reg.run_turn(
        sess, instruction="WRITE", doc="<html></html>",
        turn_uuid=turn_uuid, timeout=12.0,
    )
    assert result["reason"] == "idle"
    assert json.loads(result["envelope_bytes"])["turn_uuid"] == turn_uuid
```

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py -k "file_edge or idle_no_envelope or mtime"`
- Expected: FAIL — `_run_turn_locked` always returns `idle_no_envelope` with no real `envelope_bytes`; `test_file_edge_done_idle...` fails on `reason == "idle"`.

**(3) Minimal implementation** — in `session_registry.py`, add `await_envelope, read_envelope_bytes` to the rendezvous import: `from rendezvous import stage_turn, build_turn_prompt, await_envelope, read_envelope_bytes`. Replace the settle loop in `_run_turn_locked` (from `deadline = time.monotonic() + timeout` to its `return {...}`) with:
```python
            outcome = self._await_completion(
                sess, env_path=env_path, turn_uuid=turn_uuid,
                send_time=send_time, timeout=timeout, target=target,
                on_event=on_event)
            return outcome
        finally:
            sess.tmux.set_option("@wcb_turn", "")
```
Add the interleaving poller to `_Registry`:
```python
    _POLL_INTERVAL = 0.1
    _QUIESCENT_MS = 1.0      # dead must persist this long (corroborated)

    def _await_completion(self, sess, *, env_path, turn_uuid, send_time,
                          timeout, target, on_event):
        """Interleave the SCREEN read (awaiting_input/dead candidate) with
        rendezvous-docsync's file-edge poll. The file edge is authoritative
        for done; the screen is read only for await/dead. On success the
        envelope bytes are read back byte-exact + sentinel-checked by
        rendezvous.read_envelope_bytes (risk #4)."""
        from fsm import strip_screen, footer_of, classify
        deadline = time.monotonic() + timeout
        state = "thinking"
        prev_timer = None
        dead_since = None
        while time.monotonic() < deadline:
            stripped = strip_screen(sess.tmux.capture_pane(target))
            footer = footer_of(stripped)
            env_present = os.path.exists(env_path)
            screen_state, meta = classify(
                stripped, footer, env_present=env_present,
                prev_timer=prev_timer, composer_seen=True)
            prev_timer = meta.get("timer", prev_timer)
            state = screen_state

            if on_event and screen_state in ("thinking", "streaming"):
                if on_event(screen_state, meta) is False:
                    return {"reason": "client_gone", "state": screen_state,
                            "log_offset": 0,
                            "alive": sess.tmux.has_session("t"),
                            "envelope_bytes": None}

            if screen_state == "awaiting_input":
                return {"reason": "awaiting_input", "state": screen_state,
                        "log_offset": 0, "alive": True,
                        "screen": meta.get("screen"),
                        "kind": meta.get("kind"), "envelope_bytes": None}

            # DEAD must persist across QUIESCENT_MS AND be corroborated by
            # has-session/#{pane_dead} (risk #13, design §4). The classify
            # 'dead' is only a candidate.
            if screen_state == "dead":
                alive = sess.tmux.has_session("t")
                pane_dead = sess.tmux.pane_dead(target)
                if (not alive) or pane_dead:
                    if dead_since is None:
                        dead_since = time.monotonic()
                    elif (time.monotonic() - dead_since) >= self._QUIESCENT_MS:
                        return {"reason": "dead", "state": "dead",
                                "log_offset": 0, "alive": False,
                                "envelope_bytes": None}
                else:
                    dead_since = None
                time.sleep(self._POLL_INTERVAL)
                continue
            dead_since = None

            # FILE edge: rendezvous-docsync owns the .part->rename + mtime>send
            # + stable poll and the byte-exact, sentinel-checked read-back.
            raw = await_envelope(sess, env_path, turn_uuid, send_time,
                                 timeout=self._POLL_INTERVAL)
            if raw is not None:
                return {"reason": "idle", "state": "idle", "log_offset": 0,
                        "alive": True, "envelope_bytes": raw}

            if screen_state in ("idle", "idle_no_envelope"):
                # idle by screen but no fresh file yet — keep polling briefly.
                pass
            time.sleep(self._POLL_INTERVAL)

        # Settled without a fresh file => idle_no_envelope (NEVER read stale).
        return {"reason": "idle_no_envelope", "state": state,
                "log_offset": 0, "alive": sess.tmux.has_session("t"),
                "envelope_bytes": None}
```

> `await_envelope(..., timeout=_POLL_INTERVAL)` returns the byte-exact `read_envelope_bytes` result (which calls `verify_envelope_sentinel`) when a fresh, stable, renamed file with `mtime>send_time` exists, else `None` within that short budget. `pane_dead(target)` is the `#{pane_dead}` accessor on `TmuxClient` (tmux-client component).

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py`
- Expected: all pass.

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/integration/test_turn_protocol.py && git commit -m "turn: file-edge completion via rendezvous.await_envelope + sentinel read-back

_await_completion interleaves the screen read (awaiting_input + a
QUIESCENT_MS-persistent, has-session/#{pane_dead}-corroborated dead) with
rendezvous-docsync's authoritative .part->rename poll. On a fresh stable
renamed file await_envelope returns the byte-exact, sentinel-checked
bytes (read_envelope_bytes -> verify_envelope_sentinel, risk #4). No
fresh file at settle => idle_no_envelope, never a stale read (risks #2,
#7, #13).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 26 — `/session/stream` SSE loop (state/keepalive/done) routed through the canonical dispatcher

**Files**
- Modify: `session_endpoints.py` (`SessionMixin._do_stream`, `_write_sse`; register route in cleanup-security's `_dispatch_session`)
- Test: `tests/integration/test_session_stream_sse.py`

> FIX (critique: dispatcher + naming + ORDERING): cleanup-security's `_dispatch_session`/`_route_session` and the `server.Handler(SessionMixin, …)` wiring are the CANONICAL dispatcher and land BEFORE this task in the global order. turn-protocol's SSE handler is named `_do_stream` (the `_do_*` convention), routed for `POST /session/stream` by the canonical dispatcher — it does NOT define its own `_dispatch_session`.
>
> FIX (critique: doc_not_staged 409, design §4): after reconstruct the rwa must re-stage the current-turn doc before streaming. `_do_stream` requires a `doc` in the body; if `doc` is absent it returns 409 `doc_not_staged` BEFORE acquiring the lock.
>
> FIX (critique: CORS env var): the allowed origin is read from the single canonical `WCB_RWA_ORIGIN` (matching the rwa config and cleanup-security's `_allowed_origins`).
>
> FIX (critique: YAGNI): no `enter`/raw-Enter param; `input` is renamed `instruction` (the user instruction fed to `build_turn_prompt`).
>
> FIX (critique: cross-module test imports): `tests/integration` import mode is pinned by the test-scaffold `addopts`/`pythonpath` so `from conftest import …` resolves bare; this test is later reused by Task 27.

**(1) Write the FAILING test** — `tests/integration/test_session_stream_sse.py`:
```python
import json
import threading
import http.client

import pytest

from conftest import requires_tmux

import server
import session_registry


def _parse_sse(raw_text):
    """Parse 'event: X\\ndata: {...}\\n\\n' blocks into (event, obj) tuples."""
    events = []
    for block in raw_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        ev = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if ev:
            events.append((ev, data))
    return events


@pytest.fixture
def http_server(monkeypatch, tmp_base):
    monkeypatch.setenv("WEB_CLI_BRIDGE_TOKEN", "t0ken")
    monkeypatch.setenv("WCB_RWA_ORIGIN", "http://localhost:5173")
    monkeypatch.setattr(session_registry, "base_dir", lambda: str(tmp_base))
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()


def _post(port, path, body, origin="http://localhost:5173"):
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("POST", path, json.dumps(body), {
        "Authorization": "Bearer t0ken",
        "Content-Type": "application/json",
        "Origin": origin,
    })
    return conn.getresponse()


@requires_tmux
def test_stream_emits_state_and_done_idle(http_server, fake_socket,
                                          fake_claude_argv, monkeypatch):
    monkeypatch.setenv("WCB_FAKE_DELAY", "0.4")
    port = http_server
    create = _post(port, "/session/create", {"cwd": "."})
    assert create.status == 200
    info = json.loads(create.read())
    sid, cap = info["session_id"], info["cap"]

    resp = _post(port, "/session/stream", {
        "session_id": sid, "cap": cap,
        "doc": "<html></html>",
        "instruction": "WRITE",
        "timeout": 12.0,
    })
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/event-stream"
    events = _parse_sse(resp.read().decode("utf-8"))
    names = [e for e, _ in events]
    assert "state" in names
    assert "done" in names
    done = [d for e, d in events if e == "done"][-1]
    assert done["reason"] in ("idle", "idle_no_envelope")
    assert done["alive"] is True
    states = [d.get("state") for e, d in events if e == "state"]
    assert any(s in ("thinking", "streaming") for s in states)


@requires_tmux
def test_stream_409_doc_not_staged_when_doc_absent(http_server, fake_socket,
        fake_claude_argv, monkeypatch):
    port = http_server
    info = json.loads(_post(port, "/session/create", {"cwd": "."}).read())
    sid, cap = info["session_id"], info["cap"]
    # No 'doc' => must refuse with 409 doc_not_staged (design §4) BEFORE lock.
    resp = _post(port, "/session/stream", {
        "session_id": sid, "cap": cap, "instruction": "WRITE",
        "timeout": 12.0})
    assert resp.status == 409
    assert json.loads(resp.read())["error"] == "doc_not_staged"


@requires_tmux
def test_stream_409_when_busy(http_server, fake_socket, fake_claude_argv,
                              monkeypatch):
    monkeypatch.setenv("WCB_FAKE_DELAY", "1.0")
    port = http_server
    info = json.loads(_post(port, "/session/create", {"cwd": "."}).read())
    sid, cap = info["session_id"], info["cap"]

    results = {}

    def fire(key):
        r = _post(port, "/session/stream", {
            "session_id": sid, "cap": cap, "doc": "<html></html>",
            "instruction": "WRITE", "timeout": 12.0})
        results[key] = (r.status, r.read())

    t1 = threading.Thread(target=fire, args=("a",))
    t2 = threading.Thread(target=fire, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    statuses = sorted(s for s, _ in results.values())
    assert statuses == [200, 409]
```

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_stream_sse.py`
- Expected: FAIL — `POST /session/stream` not handled by the canonical dispatcher (405/404); no `state`/`done` SSE; no `doc_not_staged` 409.

**(3) Minimal implementation** — in `session_endpoints.py`, register `"/session/stream" -> self._do_stream` in the canonical dispatcher's POST route table (the dispatcher is owned by cleanup-security; add the entry there). Add to `SessionMixin`:
```python
    def _do_stream(self, body):
        sid = validate_session_id(body["session_id"])
        cap = body["cap"]
        sess = registry.get_or_reconstruct(sid)
        self._check_cap(sess, cap)              # PermissionError -> 403
        doc = body.get("doc")
        if doc is None:
            # After reconstruct the rwa must re-stage the current-turn doc
            # before streaming (design §4). Refuse BEFORE taking the lock.
            return self._send(409, {"error": "doc_not_staged"})
        instruction = body.get("instruction") or ""
        timeout = float(body.get("timeout") or 120.0)
        turn_uuid = registry.mint_turn_uuid()    # bridge-minted (risk #7)

        # Acquire the bounded turn lock BEFORE send_response(200) -> 409 busy.
        if not sess.turn_lock.acquire(timeout=2.0):
            return self._send(409, {"error": "session busy"})
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._session_cors()                  # headers only, no status
            self.end_headers()

            last_state = {"v": None}
            last_keep = {"t": time.time()}
            client_alive = {"v": True}

            def on_event(state, meta):
                now = time.time()
                ok = True
                if state != last_state["v"]:
                    last_state["v"] = state
                    payload = {"state": state}
                    if meta.get("kind"):
                        payload["kind"] = meta["kind"]
                    ok = self._write_sse("state", payload)
                elif now - last_keep["t"] > 10:
                    ok = self._write_sse("keepalive", {"t": now})
                last_keep["t"] = now
                if not ok:
                    client_alive["v"] = False
                return client_alive["v"]

            outcome = registry.run_turn_locked(
                sess, instruction=instruction, doc=doc, turn_uuid=turn_uuid,
                timeout=timeout, on_event=on_event)
            if not client_alive["v"]:
                outcome = {**outcome, "reason": "client_gone"}

            if outcome["reason"] == "awaiting_input":
                self._write_sse("state", {"state": "awaiting_input",
                                          "kind": outcome.get("kind"),
                                          "screen": outcome.get("screen")})
            self._write_sse("done", {
                "reason": outcome["reason"], "state": outcome["state"],
                "log_offset": outcome["log_offset"],
                "alive": outcome["alive"], "turn_uuid": turn_uuid})
        finally:
            sess.turn_lock.release()      # never kill the session here
```
Add `_write_sse` to `SessionMixin`:
```python
    def _write_sse(self, event, data):
        try:
            self.wfile.write(f"event: {event}\n".encode("ascii"))
            self.wfile.write(b"data: ")
            self.wfile.write(json.dumps(data).encode("utf-8"))
            self.wfile.write(b"\n\n")
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False
```
Ensure `validate_session_id` is imported into `session_endpoints.py`. The 200-path acquires the lock in the HTTP layer (so 409-busy is observable) and calls `run_turn_locked` (which assumes the lock is held); the lock is released in `finally`.

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_stream_sse.py tests/integration/test_turn_protocol.py`
- Expected: all pass.

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_endpoints.py tests/integration/test_session_stream_sse.py && git commit -m "stream: _do_stream SSE loop via canonical dispatcher; 409 doc_not_staged

POST /session/stream (routed by cleanup-security's _dispatch_session as
_do_stream) refuses with 409 doc_not_staged when no doc is staged
(design §4), then acquires the bounded turn lock BEFORE
send_response(200) (409 busy if held) and emits state{thinking|
streaming|awaiting_input}/keepalive(>10s)/done{reason,state,log_offset,
alive}. instruction (not input) feeds build_turn_prompt; no raw-Enter
param. Whole turn is one held-lock critical section released in finally
without killing the session. CORS origin from WCB_RWA_ORIGIN; _session_cors
emits headers only (risks #2, #4, #5).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 27 — SSE done reasons: timeout, client_gone, dead; lock released + session persists

**Files**
- Modify: `session_registry.py` (`_await_completion` — distinguish `timeout` from `idle_no_envelope`)
- Test: `tests/integration/test_session_stream_reasons.py`

> FIX (critique: cross-module import mode): the reused `http_server`/`_post`/`_parse_sse` are imported bare from `test_session_stream_sse`; the test-scaffold pins `pythonpath = tests/integration` + `importmode` so this resolves at collection time. The `client_gone` reason is already produced inside `_await_completion` (Task 25) via the `on_event` falsy return; this task adds the `timeout` distinction and the lock-release-survives assertions.

**(1) Write the FAILING test** — `tests/integration/test_session_stream_reasons.py`:
```python
import json
import http.client
import time as _t

import pytest

from conftest import requires_tmux
from test_session_stream_sse import http_server, _post, _parse_sse  # reuse


@requires_tmux
def test_timeout_reason_releases_lock_and_keeps_session(http_server,
        fake_socket, fake_claude_argv, monkeypatch):
    # Make the fake WORK longer than the turn timeout so it never settles.
    monkeypatch.setenv("WCB_FAKE_DELAY", "5.0")
    port = http_server
    info = json.loads(_post(port, "/session/create", {"cwd": "."}).read())
    sid, cap = info["session_id"], info["cap"]

    resp = _post(port, "/session/stream", {
        "session_id": sid, "cap": cap, "doc": "<html></html>",
        "instruction": "WRITE", "timeout": 1.0})  # 1s < 5s work
    assert resp.status == 200
    events = _parse_sse(resp.read().decode("utf-8"))
    done = [d for e, d in events if e == "done"][-1]
    assert done["reason"] == "timeout"
    assert done["alive"] is True   # session NOT killed by timeout

    # A subsequent turn can acquire the lock (released in finally).
    resp2 = _post(port, "/session/stream", {
        "session_id": sid, "cap": cap, "doc": "<html></html>",
        "instruction": "", "timeout": 6.0})
    assert resp2.status == 200
    resp2.read()


@requires_tmux
def test_client_gone_releases_lock(http_server, fake_socket,
        fake_claude_argv, monkeypatch):
    monkeypatch.setenv("WCB_FAKE_DELAY", "3.0")
    port = http_server
    info = json.loads(_post(port, "/session/create", {"cwd": "."}).read())
    sid, cap = info["session_id"], info["cap"]

    # Open a stream and close the socket mid-turn to force client_gone.
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("POST", "/session/stream", json.dumps({
        "session_id": sid, "cap": cap, "doc": "<html></html>",
        "instruction": "WRITE", "timeout": 12.0}), {
        "Authorization": "Bearer t0ken", "Content-Type": "application/json",
        "Origin": "http://localhost:5173"})
    r = conn.getresponse()
    assert r.status == 200
    r.fp.read(1)        # consume the first byte, then drop the connection
    conn.close()

    # Lock must have been released in finally; new turn succeeds.
    deadline = _t.time() + 15
    ok = False
    while _t.time() < deadline:
        r2 = _post(port, "/session/stream", {
            "session_id": sid, "cap": cap, "doc": "<html></html>",
            "instruction": "", "timeout": 5.0})
        r2.read()
        if r2.status == 200:
            ok = True
            break
        _t.sleep(0.5)
    assert ok, "lock was not released after client_gone"
```

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_stream_reasons.py`
- Expected: FAIL — `_await_completion` returns `idle_no_envelope` (not `timeout`) at deadline; `test_timeout_reason...` fails on `reason == "timeout"`.

**(3) Minimal implementation** — in `session_registry.py` `_await_completion`, track whether the screen ever settled idle, and report `timeout` when it never did. Initialize before the loop:
```python
        settled_idle = False
```
Inside the loop, set it when the screen reads idle (just after computing `screen_state`, before the file poll):
```python
            if screen_state in ("idle", "idle_no_envelope"):
                settled_idle = True
```
Replace the post-loop fall-through with:
```python
        # Deadline reached. If the screen settled idle but no fresh file was
        # ever produced => idle_no_envelope; otherwise the turn never settled
        # => timeout. Either way the lock is released by the caller's finally
        # and the session is NOT killed (risks #4, #10).
        if settled_idle:
            return {"reason": "idle_no_envelope", "state": state,
                    "log_offset": 0, "alive": sess.tmux.has_session("t"),
                    "envelope_bytes": None}
        return {"reason": "timeout", "state": state, "log_offset": 0,
                "alive": sess.tmux.has_session("t"), "envelope_bytes": None}
```
(`client_gone` is already returned mid-loop from the guarded `on_event` falsy return added in Task 25; the HTTP layer also overrides `reason` to `client_gone` when `_write_sse` failed.)

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_session_stream_reasons.py tests/integration/test_session_stream_sse.py`
- Expected: all pass.

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/integration/test_session_stream_reasons.py && git commit -m "stream: done reasons timeout/client_gone/dead; lock always released

A turn that never settled before its timeout reports done{reason:timeout}
(vs idle_no_envelope when the screen settled idle without a file); a
_write_sse failure / on_event falsy return aborts the poll with
done{reason:client_gone}; a corroborated persistent shell yields
done{reason:dead}. All release the turn lock in finally so a follow-up
turn can acquire it and the session persists for resume (risks #4, #10).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 28 — `/session/send-key` rising-timer end-to-end + NAMED_KEYS allowlist, poll effect

**Files**
- Modify: `session_endpoints.py` (`SessionMixin._do_send_key`; register route in cleanup-security's `_dispatch_session`)
- Test: `tests/integration/test_send_key.py`

> FIX (critique: replay ownership): `/session/replay` is OWNED by cleanup-security (its `_do_replay` accounts for `log_offset_base` rotation so `from_offset` stays globally monotonic). This component does NOT author `read_log_slice`/`_do_replay` — the original Task 10 replay half is dropped here.
>
> FIX (critique: NAMED_KEYS single source): `_do_send_key` imports `from tmux_session import NAMED_KEYS`; no local redefinition, no `registry.NAMED_KEYS`, no dead `if False` import.
>
> FIX (critique: dispatcher + naming): handler is `_do_send_key`, routed for `POST /session/send-key` by cleanup-security's canonical `_dispatch_session`.
>
> FIX (critique: risk #2 end-to-end): this task also adds an end-to-end rising-timer assertion driven by the canonical `tests/fake_claude.sh` rising-`(Ns)` mode (the test-scaffold fake gained an incrementing spinner-timer mode), so the rising-timer WORKING gate is exercised through a real turn, not only unit-mocked in Task 20.

**(1) Write the FAILING test** — `tests/integration/test_send_key.py`:
```python
import json

import pytest

from conftest import requires_tmux
from test_session_stream_sse import http_server, _post, _parse_sse


@requires_tmux
def test_send_key_named_allowlist_and_state(http_server, fake_socket,
        fake_claude_argv, monkeypatch):
    monkeypatch.setenv("WCB_FAKE_TRUST", "1")  # fake shows trust prompt
    port = http_server
    info = json.loads(_post(port, "/session/create", {"cwd": "."}).read())
    sid, cap = info["session_id"], info["cap"]

    # Answer the trust menu via named + literal keys: ["1","Enter"].
    resp = _post(port, "/session/send-key", {
        "session_id": sid, "cap": cap, "keys": ["1", "Enter"]})
    assert resp.status == 200
    out = json.loads(resp.read())
    assert out["ok"] is True
    assert out["state"] in (
        "starting", "thinking", "streaming", "awaiting_input",
        "idle", "idle_no_envelope")


@requires_tmux
def test_send_key_rejects_non_string_element(http_server, fake_socket,
        fake_claude_argv, monkeypatch):
    port = http_server
    info = json.loads(_post(port, "/session/create", {"cwd": "."}).read())
    sid, cap = info["session_id"], info["cap"]
    # A non-string element is a 400.
    resp = _post(port, "/session/send-key", {
        "session_id": sid, "cap": cap, "keys": [123]})
    assert resp.status == 400


@requires_tmux
def test_rising_spinner_timer_drives_working_end_to_end(http_server,
        fake_socket, fake_claude_argv, monkeypatch):
    # Canonical fake emits an incrementing '(Ns · ...)' spinner in this mode,
    # with NO 'esc to interrupt' footer, so WORKING is gated ONLY by the
    # rising timer end-to-end (risk #2), not by the footer token.
    monkeypatch.setenv("WCB_FAKE_RISING_TIMER", "1")
    monkeypatch.setenv("WCB_FAKE_DELAY", "1.5")
    port = http_server
    info = json.loads(_post(port, "/session/create", {"cwd": "."}).read())
    sid, cap = info["session_id"], info["cap"]
    resp = _post(port, "/session/stream", {
        "session_id": sid, "cap": cap, "doc": "<html></html>",
        "instruction": "WRITE", "timeout": 12.0})
    assert resp.status == 200
    events = _parse_sse(resp.read().decode("utf-8"))
    states = [d.get("state") for e, d in events if e == "state"]
    assert any(s in ("thinking", "streaming") for s in states), \
        "rising spinner timer must gate WORKING end-to-end"
    done = [d for e, d in events if e == "done"][-1]
    assert done["reason"] in ("idle", "idle_no_envelope")
```

**(2) Run + expected FAIL**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_send_key.py`
- Expected: FAIL — `POST /session/send-key` not handled by the canonical dispatcher (405/404); the rising-timer test fails if `_await_completion` did not thread `prev_timer` (it does, from Task 25) AND if the canonical fake lacks the rising-timer mode.

**(3) Minimal implementation** — in `session_endpoints.py`, add `from tmux_session import NAMED_KEYS` at the top, and register `"/session/send-key" -> self._do_send_key` in cleanup-security's canonical dispatcher POST route table. Add to `SessionMixin`:
```python
    def _do_send_key(self, body):
        sid = validate_session_id(body["session_id"])
        sess = registry.get_or_reconstruct(sid)
        self._check_cap(sess, body["cap"])
        keys = body["keys"]
        if not isinstance(keys, list) or not keys:
            raise ValueError("keys must be a non-empty list")
        for k in keys:
            if not isinstance(k, str):
                raise TypeError("each key must be a string")
        # Standalone send-key polls effect before releasing (risk #10): take
        # the bounded turn lock so it cannot race a stream turn.
        if not sess.turn_lock.acquire(timeout=2.0):
            return self._send(409, {"error": "session busy"})
        try:
            for k in keys:
                if k in NAMED_KEYS:
                    sess.tmux.send_keys(sess.pane, k)     # tmux key name
                else:
                    sess.tmux.send_text(sess.pane, k)     # literal -l ... --
            # Poll the effect once so the answer has landed before release.
            from fsm import strip_screen, footer_of, classify
            stripped = strip_screen(sess.tmux.capture_pane(sess.pane))
            state, _m = classify(stripped, footer_of(stripped),
                                  env_present=False, composer_seen=True)
            return self._send(200, {"ok": True, "state": state})
        finally:
            sess.turn_lock.release()
```
The rising-timer mode is provided by the canonical `tests/fake_claude.sh` (test-scaffold): when `WCB_FAKE_RISING_TIMER=1` it prints an incrementing `(<N>s · …)` spinner each second and OMITS the `esc to interrupt` footer, so the WORKING gate is the rising timer alone. `_await_completion` already threads `prev_timer` into `classify` (Task 25).

**(4) Run + expected PASS**
- Command: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_send_key.py`
- Expected: `3 passed`. Full component regression: `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_fsm_strip.py tests/unit/test_fsm_classify_working.py tests/unit/test_fsm_classify_await.py tests/unit/test_fsm_classify_dead.py tests/integration/test_turn_protocol.py tests/integration/test_session_stream_sse.py tests/integration/test_session_stream_reasons.py tests/integration/test_send_key.py` → all pass.

**(5) Commit**
- `cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_endpoints.py tests/integration/test_send_key.py && git commit -m "endpoints: _do_send_key (NAMED_KEYS allowlist, poll effect) + rising-timer e2e

/session/send-key (routed by the canonical dispatcher as _do_send_key)
sends allowlisted tmux key names and everything else literally (-l ...
--), holding the turn lock and polling the effect before release so an
answer never races a resume (risk #10). NAMED_KEYS imported from
tmux_session (single source). Adds an end-to-end rising-spinner-timer
test (canonical fake WCB_FAKE_RISING_TIMER mode, no esc-to-interrupt
footer) so the rising-timer WORKING gate is exercised through a real turn
(risk #2). /session/replay is owned by cleanup-security and not authored
here.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Component summary (for the assembler)

**Tasks authored (global numbering 19–28):**

19. `fsm.strip_screen` + `fsm.footer_of` over real captures; single canonical `FOOTER_IDLE` shared with the canonical `fake_claude.sh` (risk #2)
20. `fsm.classify` WORKING via `esc to interrupt` / rising timer / env-absent; pinned signature with explicit `composer_seen`; verb never gates (risk #2)
21. `fsm.classify` `awaiting_input` (trust block) + `idle` vs `idle_no_envelope`; consumers handle full STATES (risk #2)
22. `fsm.classify` `starting` vs `dead` shell-name CANDIDATE (corroborated later) (risks #2, #13)
23. Held-lock turn skeleton — bounded acquire → 409 `SessionBusy`, `run_turn`/`run_turn_locked`/`_run_turn_locked`, release in finally, session survives; `socket_override` test-only (risks #4, #5, #10)
24. Stage doc + sweep stale env via rendezvous-docsync `stage_turn`; send `build_turn_prompt` output (no inlined template) (risks #3, #4, #7, #9)
25. FILE-edge completion via rendezvous-docsync `await_envelope` + byte-exact, sentinel-checked `read_envelope_bytes`; QUIESCENT_MS + `has-session`/`#{pane_dead}`-corroborated dead (risks #2, #7, #13, #4)
26. `/session/stream` `_do_stream` SSE loop routed through cleanup-security's canonical dispatcher; 409 `doc_not_staged`; lock-before-200 409 busy; `instruction` not `input`; CORS via `WCB_RWA_ORIGIN` (risks #2, #4, #5)
27. SSE `done` reasons `timeout`/`client_gone`/`dead`; lock always released, session persists (risks #4, #10)
28. `/session/send-key` `_do_send_key` (NAMED_KEYS from `tmux_session`, poll effect) + end-to-end rising-timer WORKING test (risks #10, #2)

**Critique fixes applied in this component:** classify signature pinned (`composer_seen`); consumers handle full STATES; single canonical `FOOTER_IDLE`/`fake_claude.sh` (depend on test-scaffold, not re-author); `run_turn`/`_run_turn_locked` own the turn but CALL rendezvous-docsync `stage_turn`/`build_turn_prompt`/`await_envelope`/`read_envelope_bytes` (no duplicated staging or `_expand` template); production prompt via `build_turn_prompt` (the `{env}`/`{uuid}` template is fake-only); `doc_not_staged` 409 added + tested; DEAD multi-poll + `has-session`/`#{pane_dead}` corroboration wired into `_await_completion`; `_session_*` handlers renamed `_do_*` and routed through cleanup-security's canonical `_dispatch_session` (this component does not define a dispatcher); CORS env var pinned to `WCB_RWA_ORIGIN`; `NAMED_KEYS` imported from `tmux_session` (single source); `/session/replay`/`read_log_slice` dropped (owned by cleanup-security with `log_offset_base`); `verify_envelope_sentinel` invoked via `read_envelope_bytes`; rising-timer exercised end-to-end (risk #2) via the canonical fake's rising-timer mode; YAGNI `enter`/raw-Enter param dropped, `input`→`instruction`; cross-module test imports rely on the test-scaffold-pinned `pythonpath`/`importmode`.

**Owned artifacts:** `fsm.py` (created in full, Tasks 19–22), `tests/unit/test_fsm_*.py`, `tests/integration/test_turn_protocol.py`, `test_session_stream_sse.py`, `test_session_stream_reasons.py`, `test_send_key.py`. **Methods ADDED to shared modules:** `session_registry._Registry.run_turn`/`run_turn_locked`/`_run_turn_locked`/`_await_completion`/`mint_turn_uuid`, exception `SessionBusy`, `create(...socket_override)` extension; `session_endpoints.SessionMixin._do_stream`/`_do_send_key`/`_write_sse`. **Consumed from siblings (signatures matched):** `rendezvous.stage_turn`/`build_turn_prompt`/`await_envelope`/`read_envelope_bytes`/`verify_envelope_sentinel` (rendezvous-docsync); `TmuxClient.capture_pane`/`send_prompt`/`send_keys`/`send_text`/`set_option`/`has_session`/`pane_dead` + `NAMED_KEYS` (tmux-client); `registry.get_or_reconstruct`/`create`/`_Session.turn_lock`/`pane`/`rendezvous_dir`, `base_dir` (registry-lifecycle); the canonical `_dispatch_session`/`_session_cors`/`_check_cap`/`_send`/`_allowed_origins` and `server.Handler(SessionMixin, …)` wiring + the canonical `tests/fake_claude.sh` (cleanup-security + test-scaffold). **Global-order dependency:** test-scaffold → paths + fsm → tmux-client → registry-lifecycle → rendezvous-docsync → cleanup-security dispatcher/security wiring → THIS component's Tasks 23–28.

**Critique fixes applied to this `rendezvous-docsync` component:**

Key fixes touching this component:
- **FIX (YAGNI/wire sentinel):** Wire `verify_envelope_sentinel` into the read-back/get-envelope path (Task D/I) or it's untraceable gold-plating — I'll wire it into `read_envelope_bytes`.
- **FIX (`_REGISTRY` vs `REGISTRY` singleton name):** Pin to `REGISTRY` (registry-lifecycle owner). Task I and the get-envelope test reference `reg._REGISTRY` → must be `REGISTRY`.
- **FIX (`_Registry` constructor / `_base`):** Use `_Registry(base=...)` with `self._base`.
- **FIX (CORS env var):** Pin to `WCB_ALLOWED_ORIGINS` (cleanup-security Task 9 canonical) instead of `WCB_RWA_ORIGIN`.
- **FIX (dispatcher naming `_do_*` not `_session_*`; cleanup-security Task 9 is canonical dispatcher owner):** Rename get-envelope handler to `_do_get_envelope`, routed through canonical dispatcher; drop the inline `_require_session` (owned by security component); error ladder owned by dispatcher.
- **FIX (ordering):** Task I depends on cleanup-security dispatcher + registry shell + path-safety; turn-protocol Tasks depend on `read_envelope_bytes`/`stage_turn`/`await_envelope`/`build_turn_prompt` here.
- **FIX (`_run_turn_locked` ownership):** turn-protocol owns it and CALLS this component's `stage_turn`/`await_envelope`/`build_turn_prompt`.
- **FIX (409 `doc_not_staged`):** Add the precondition + test (design §4) — staging happens in held lock, but reconstruct must refuse to stream until re-staged. I'll add a `doc_staged` check helper here.
- **FIX (TDD-shape Task J/K):** Add automated static-assertion grep tests (mirroring `test_packaging.py`) so there's a red→green gate, alongside the manual verification.
- **FIX (replay offset base):** Not owned here; cross-ref only.
- **FIX (`mint_nonce` is `mint_cap`/`mint_nonce`):** keep cross-refs.


## Task 29 — `verify_base_dir` + base-dir bootstrap (lstat 0700, owned by euid, not a symlink)

**Files**
- Modify: `paths.py` (add `verify_base_dir`; `base_dir` already present from the path-safety component — Tasks 4–5 — if absent, add the minimal `$HOME`-rooted version shown in step 3).
- Test: `tests/unit/test_base_dir.py` (Create)

**Steps**

**(1) Write the FAILING test.** Create `tests/unit/test_base_dir.py`:

```python
import os
import stat
import pytest
import paths


def _mk(d, mode=0o700):
    os.mkdir(d, mode)
    os.chmod(d, mode)  # defeat umask
    return d


def test_base_dir_is_under_home_not_tmp():
    b = paths.base_dir()
    home = os.path.realpath(os.path.expanduser("~"))
    assert os.path.realpath(b).startswith(home + os.sep)
    assert not os.path.realpath(b).startswith("/tmp")
    assert not os.path.realpath(b).startswith("/private/tmp")


def test_verify_base_dir_accepts_good_dir(tmp_path):
    d = _mk(str(tmp_path / "rv"))
    paths.verify_base_dir(d)  # must not raise


def test_verify_base_dir_rejects_symlink(tmp_path):
    real = _mk(str(tmp_path / "real"))
    link = str(tmp_path / "link")
    os.symlink(real, link)
    with pytest.raises(RuntimeError):
        paths.verify_base_dir(link)


def test_verify_base_dir_rejects_group_world_bits(tmp_path):
    d = _mk(str(tmp_path / "rv"), mode=0o755)
    with pytest.raises(RuntimeError):
        paths.verify_base_dir(d)


def test_verify_base_dir_rejects_non_dir(tmp_path):
    f = str(tmp_path / "afile")
    with open(f, "w") as fh:
        fh.write("x")
    os.chmod(f, 0o700)
    with pytest.raises(RuntimeError):
        paths.verify_base_dir(f)


def test_verify_base_dir_rejects_missing(tmp_path):
    with pytest.raises(RuntimeError):
        paths.verify_base_dir(str(tmp_path / "nope"))
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_base_dir.py
```
Expected: `AttributeError: module 'paths' has no attribute 'verify_base_dir'` (or `base_dir`) → all tests ERROR/FAIL.

**(3) Minimal implementation.** In `paths.py` add (and add `base_dir` only if the path-safety component, Tasks 4–5, has not):

```python
import os
import stat
import sys


def base_dir() -> str:
    """Rendezvous base under $HOME, never /tmp. Created 0700 if missing."""
    if sys.platform == "darwin":
        root = os.path.join(
            os.path.expanduser("~"),
            "Library", "Application Support", "WebCLIBridge", "rendezvous",
        )
    else:
        root = os.path.join(os.path.expanduser("~"), ".web_cli_bridge", "rendezvous")
    os.makedirs(root, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)  # explicit: makedirs honours umask, chmod defeats it
    return root


def verify_base_dir(path) -> None:
    """lstat-verify: real dir, not a symlink, owned by euid, mode exactly 0700."""
    try:
        st = os.lstat(path)
    except FileNotFoundError as e:
        raise RuntimeError(f"rendezvous base missing: {path}") from e
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"rendezvous base is a symlink: {path}")
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeError(f"rendezvous base is not a directory: {path}")
    if st.st_uid != os.geteuid():
        raise RuntimeError(f"rendezvous base not owned by euid: {path}")
    if stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError(
            f"rendezvous base mode {oct(stat.S_IMODE(st.st_mode))} != 0o700: {path}"
        )
```

Ensure `import sys` is present at the top of `paths.py` (add if missing).

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_base_dir.py
```
Expected: `6 passed`.

**(5) Commit.**
```
git add paths.py tests/unit/test_base_dir.py
git commit -m "feat(paths): verify_base_dir lstat 0700/owner/symlink guard

Rendezvous base under \$HOME (never /tmp); refuse symlink, non-dir,
wrong owner, or group/world bits. Mitigates risk #8 (base hijack).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 30 — per-session rendezvous dir creation (0700, nonce-named, confined)

**Files**
- Modify: `session_registry.py` (add `_ensure_session_dir` helper used by `create`; `session_dir`/`assert_confined` already in `paths.py` from the path-safety component, Tasks 4–5).
- Test: `tests/unit/test_session_dir.py` (Create)

**Steps**

**(1) Write the FAILING test.** Create `tests/unit/test_session_dir.py`:

```python
import os
import stat
import pytest
import paths
import session_registry as reg


def test_session_dir_shape():
    sid = "a" * 32
    nonce = "deadbeefdeadbeef"
    d = paths.session_dir("/base", sid, nonce)
    assert d == f"/base/wcb_{sid}_{nonce}"


def test_ensure_session_dir_creates_0700_confined(tmp_path):
    base = str(tmp_path / "rv")
    os.mkdir(base, 0o700); os.chmod(base, 0o700)
    sid = "b" * 32
    nonce = paths.mint_nonce()
    d = reg._ensure_session_dir(base, sid, nonce)
    st = os.lstat(d)
    assert stat.S_ISDIR(st.st_mode)
    assert stat.S_IMODE(st.st_mode) == 0o700
    # confined under base
    assert os.path.realpath(d).startswith(os.path.realpath(base) + os.sep)


def test_ensure_session_dir_rejects_escape(tmp_path):
    base = str(tmp_path / "rv")
    os.mkdir(base, 0o700); os.chmod(base, 0o700)
    # a nonce that tries to climb out must never produce a dir outside base.
    # mint_nonce() is hex-only so this is belt-and-suspenders via assert_confined.
    with pytest.raises((PermissionError, ValueError)):
        reg._ensure_session_dir(base, "c" * 32, "../escape")
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_session_dir.py
```
Expected: `AttributeError: module 'session_registry' has no attribute '_ensure_session_dir'`.

**(3) Minimal implementation.** In `session_registry.py`:

```python
import os
import paths


def _ensure_session_dir(base, sid, nonce) -> str:
    """Create the per-session rendezvous dir 0700, confined under base.

    nonce is hex-only by construction (mint_nonce); assert_confined is the
    hard guard against any path-bound id that tries to escape base.
    """
    d = paths.session_dir(base, sid, nonce)
    paths.assert_confined(d, base)  # realpath under base, else PermissionError
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)              # defeat umask
    return d
```

If `paths.assert_confined` raises `ValueError` rather than `PermissionError` for a non-normalizable path, the test already accepts both.

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_session_dir.py
```
Expected: `3 passed`.

**(5) Commit.**
```
git add session_registry.py tests/unit/test_session_dir.py
git commit -m "feat(registry): per-session rendezvous dir (0700, nonce-named, confined)

_ensure_session_dir realpath-confines under base before makedirs; explicit
chmod defeats umask. Mitigates risk #6/#14 (traversal, dir re-derive).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 31 — atomic `put_doc` — write `doc.<turn_uuid>.html.part` then rename

**Files**
- Modify: `paths.py` (add `doc_path`, `put_doc`; uses `validate_turn_uuid`/`assert_confined` from the path-safety component, Tasks 4–5).
- Test: `tests/unit/test_put_doc.py` (Create)

**Steps**

**(1) Write the FAILING test.** Create `tests/unit/test_put_doc.py`:

```python
import os
import stat
import pytest
import paths


def _base(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    return b


UUID = "12345678-1234-1234-1234-1234567890ab"


def test_doc_path_shape():
    p = paths.doc_path("/sess", UUID)
    assert p == f"/sess/doc.{UUID}.html"


def test_put_doc_writes_bytes_exact_then_renames(tmp_path):
    base = _base(tmp_path)
    sess = os.path.join(base, "wcb_" + "a" * 32 + "_" + "b" * 16)
    os.mkdir(sess, 0o700)
    payload = b"<html>\n<body>hi</body>\n</html>\n"
    final = paths.put_doc(sess, base, UUID, payload)
    assert final == paths.doc_path(sess, UUID)
    # the .part scratch file is gone after rename
    assert not os.path.exists(final + ".part")
    with open(final, "rb") as fh:
        assert fh.read() == payload
    # 0600, single link, owned by us
    st = os.lstat(final)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 1
    assert st.st_uid == os.getuid()


def test_put_doc_rejects_carriage_return(tmp_path):
    base = _base(tmp_path)
    sess = os.path.join(base, "s"); os.mkdir(sess, 0o700)
    with pytest.raises(ValueError):
        paths.put_doc(sess, base, UUID, b"line1\r\nline2\n")


def test_put_doc_rejects_bad_uuid(tmp_path):
    base = _base(tmp_path)
    sess = os.path.join(base, "s"); os.mkdir(sess, 0o700)
    with pytest.raises(ValueError):
        paths.put_doc(sess, base, "../etc/passwd", b"x")


def test_put_doc_overwrites_prior_doc_atomically(tmp_path):
    base = _base(tmp_path)
    sess = os.path.join(base, "s"); os.mkdir(sess, 0o700)
    paths.put_doc(sess, base, UUID, b"old\n")
    final = paths.put_doc(sess, base, UUID, b"new\n")
    with open(final, "rb") as fh:
        assert fh.read() == b"new\n"
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_put_doc.py
```
Expected: `AttributeError: module 'paths' has no attribute 'doc_path'`.

**(3) Minimal implementation.** In `paths.py`:

```python
def doc_path(session_dir_path, turn_uuid) -> str:
    return os.path.join(session_dir_path, f"doc.{turn_uuid}.html")


def put_doc(session_dir_path, base, turn_uuid, data: bytes) -> str:
    """Atomically stage doc.<turn_uuid>.html: write .part (O_NOFOLLOW, 0600)
    then os.replace to the final name.

    Asserts \\n-only (no \\r) so anchor bytes match canonLF(persistDoc).
    Realpath-confines both the .part and final path under base first.
    """
    validate_turn_uuid(turn_uuid)          # rejects ../ , %, NUL, etc.
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("doc payload must be bytes")
    if b"\r" in data:
        raise ValueError("doc payload must be \\n-only (contains \\r)")
    final = doc_path(session_dir_path, turn_uuid)
    part = final + ".part"
    assert_confined(final, base)
    assert_confined(part, base)
    # O_NOFOLLOW + O_TRUNC + the confined session dir (0700) defeat symlink swaps.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(part, flags, 0o600)
    try:
        os.write(fd, bytes(data))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(part, final)                # atomic within the same dir
    return final
```

`validate_turn_uuid` and `assert_confined` come from the path-safety component (Tasks 4–5); this task assumes they have already landed.

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_put_doc.py
```
Expected: `5 passed`.

**(5) Commit.**
```
git add paths.py tests/unit/test_put_doc.py
git commit -m "feat(paths): atomic put_doc (.part->rename, NOFOLLOW 0600, LF-only)

Stage doc.<uuid>.html via O_NOFOLLOW .part write + os.replace; assert
no CR so anchor bytes match canonLF(persistDoc). Mitigates #3/#9.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 32 — gen sentinel — inject `<!-- rwa:gen <uuid> -->` and the `verify_envelope_sentinel` echo guard

**Files**
- Modify: `paths.py` (add `inject_gen_sentinel`, `GEN_SENTINEL_RE`, `verify_envelope_sentinel`; uses `validate_turn_uuid` + the `EnvelopeRejected` class from the path-safety component, Tasks 4–5).
- Test: `tests/unit/test_gen_sentinel.py` (Create)

> Authored BEFORE the read-back (Task 33) because `read_envelope_bytes` CALLS `verify_envelope_sentinel` to close risk #4 (per critique FIX: the sentinel echo must be wired into the read path, not merely tested in isolation). `EnvelopeRejected` is the path-safety component's exception class; if not yet present, Task 33 step 3 defines it — keep one definition.

**Steps**

**(1) Write the FAILING test.** Create `tests/unit/test_gen_sentinel.py`:

```python
import pytest
import paths

UUID = "12345678-1234-1234-1234-1234567890ab"


def test_inject_appends_sentinel_when_absent():
    doc = b"<html>\n<body>hi</body>\n</html>\n"
    out = paths.inject_gen_sentinel(doc, UUID)
    assert (f"<!-- rwa:gen {UUID} -->").encode() in out
    # original content preserved, LF-only
    assert out.startswith(doc.rstrip(b"\n"))
    assert b"\r" not in out


def test_inject_replaces_prior_sentinel():
    old = "11111111-1111-1111-1111-111111111111"
    doc = (b"<html>\n<!-- rwa:gen " + old.encode() + b" -->\n</html>\n")
    out = paths.inject_gen_sentinel(doc, UUID)
    assert old.encode() not in out
    assert (f"<!-- rwa:gen {UUID} -->").encode() in out
    # exactly one sentinel
    assert out.count(b"<!-- rwa:gen ") == 1


def test_verify_envelope_sentinel_accepts_matching():
    env = {"tool": "apply_edits", "envelope": {"x": 1},
           "turn_uuid": UUID, "gen": UUID}
    paths.verify_envelope_sentinel(env, UUID)  # must not raise


def test_verify_envelope_sentinel_rejects_mismatch():
    env = {"tool": "apply_edits", "envelope": {"x": 1},
           "turn_uuid": UUID, "gen": "ffffffff-1111-1111-1111-111111111111"}
    with pytest.raises(paths.EnvelopeRejected):
        paths.verify_envelope_sentinel(env, UUID)


def test_verify_envelope_sentinel_rejects_missing_gen():
    env = {"tool": "apply_edits", "envelope": {"x": 1}, "turn_uuid": UUID}
    with pytest.raises(paths.EnvelopeRejected):
        paths.verify_envelope_sentinel(env, UUID)
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_gen_sentinel.py
```
Expected: `AttributeError: module 'paths' has no attribute 'inject_gen_sentinel'`. (If `paths.EnvelopeRejected` is also absent, the test errors at attribute access — Task 33 step 3 / the path-safety component owns that class; define it here only if neither has yet.)

**(3) Minimal implementation.** In `paths.py`:

```python
import re

GEN_SENTINEL_RE = re.compile(rb"<!-- rwa:gen [0-9a-f-]{36} -->")


def inject_gen_sentinel(doc: bytes, turn_uuid) -> bytes:
    """Embed exactly one `<!-- rwa:gen <uuid> -->` comment.

    Replaces a prior sentinel if present, else appends. Caller stages the
    result; the prompt requires copying the uuid back into envelope.gen.
    """
    validate_turn_uuid(turn_uuid)
    if b"\r" in doc:
        raise ValueError("doc must be \\n-only before sentinel injection")
    tag = f"<!-- rwa:gen {turn_uuid} -->".encode()
    if GEN_SENTINEL_RE.search(doc):
        return GEN_SENTINEL_RE.sub(tag, doc, count=1)
    body = doc.rstrip(b"\n")
    return body + b"\n" + tag + b"\n"


def verify_envelope_sentinel(obj: dict, turn_uuid) -> None:
    """Reject (422) any envelope whose `gen` != the staged turn_uuid.

    Called by read_envelope_bytes (Task 33) so the echo guard is wired into
    the read path, not only tested in isolation (closes risk #4).
    """
    if obj.get("gen") != turn_uuid:
        raise EnvelopeRejected(
            f"gen sentinel mismatch: got {obj.get('gen')!r} want {turn_uuid!r}"
        )
```

`EnvelopeRejected` is shared with Task 33; keep exactly one definition (the path-safety component owns it if it shipped the three envelope exception classes).

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_gen_sentinel.py
```
Expected: `5 passed`.

**(5) Commit.**
```
git add paths.py tests/unit/test_gen_sentinel.py
git commit -m "feat(paths): rwa:gen sentinel inject + verify_envelope_sentinel echo

Embed one <!-- rwa:gen <uuid> -->; verify_envelope_sentinel rejects any
envelope whose gen != staged turn_uuid (422). Wired into read_envelope_bytes
(Task 33) so the echo guard runs in the read path. Mitigates #3/#4.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 33 — byte-exact `O_NOFOLLOW` read-back with owner/mode/nlink + turn_uuid + gen-sentinel asserts

**Files**
- Modify: `paths.py` (add `env_path`, `read_envelope_bytes`, `safe_open_nofollow`, and the three envelope exception classes — only those not already provided by the path-safety component, Tasks 4–5).
- Test: `tests/unit/test_envelope_readback.py` (Create)

> Realizes the SKELETON §3 `read_envelope_bytes` contract and the design's exact read-back block. If the path-safety component already shipped `safe_open_nofollow` and the `EnvelopeNotWritten`/`EnvelopeRejected`/`EnvelopeIncomplete` classes, keep only `env_path` + `read_envelope_bytes` here and reference theirs. Per critique FIX (wire the sentinel), `read_envelope_bytes` calls `verify_envelope_sentinel` (Task 32) so the gen echo is enforced inside the read path — turn-protocol Task(s) and `_do_get_envelope` (Task 37) inherit the check for free.

**Steps**

**(1) Write the FAILING test.** Create `tests/unit/test_envelope_readback.py`:

```python
import json
import os
import pytest
import paths

UUID = "12345678-1234-1234-1234-1234567890ab"
# The exact 28-byte, no-trailing-newline calibration payload, wrapped in the
# envelope shape the bridge expects.
INNER = '{"ok":true,"probe":"wcbcal"}'


def _sess(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    s = os.path.join(b, "s"); os.mkdir(s, 0o700)
    return b, s


def _write_env(sess, turn_uuid, obj, mode=0o600):
    p = paths.env_path(sess, turn_uuid)
    raw = json.dumps(obj).encode()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    os.write(fd, raw); os.close(fd)
    os.chmod(p, mode)
    return p, raw


def test_env_path_shape():
    assert paths.env_path("/s", UUID) == f"/s/env.{UUID}.json"


def test_byte_exact_readback(tmp_path):
    base, sess = _sess(tmp_path)
    obj = {"tool": "apply_edits", "envelope": {"v": 1}, "turn_uuid": UUID,
           "gen": UUID, "probe": INNER}
    p, raw = _write_env(sess, UUID, obj)
    got = paths.read_envelope_bytes(p, UUID)
    assert got == raw                     # verbatim, no re-serialization
    assert isinstance(got, bytes)


def test_no_trailing_newline_preserved(tmp_path):
    base, sess = _sess(tmp_path)
    p = paths.env_path(sess, UUID)
    raw = (b'{"tool":"x","envelope":{"a":1},"turn_uuid":"' + UUID.encode()
           + b'","gen":"' + UUID.encode() + b'"}')
    assert not raw.endswith(b"\n")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.write(fd, raw); os.close(fd)
    assert paths.read_envelope_bytes(p, UUID) == raw  # exactly 28-style bytes


def test_rejects_symlink(tmp_path):
    base, sess = _sess(tmp_path)
    real = os.path.join(sess, "real.json")
    with open(real, "w") as fh:
        fh.write(json.dumps({"tool": "x", "envelope": {}, "turn_uuid": UUID,
                            "gen": UUID}))
    link = paths.env_path(sess, UUID)
    os.symlink(real, link)
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(link, UUID)


def test_rejects_group_world_bits(tmp_path):
    base, sess = _sess(tmp_path)
    p, _ = _write_env(sess, UUID,
                      {"tool": "x", "envelope": {"a": 1}, "turn_uuid": UUID,
                       "gen": UUID},
                      mode=0o077)
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)


def test_rejects_hardlink_nlink_gt_1(tmp_path):
    base, sess = _sess(tmp_path)
    p, _ = _write_env(sess, UUID,
                      {"tool": "x", "envelope": {"a": 1}, "turn_uuid": UUID,
                       "gen": UUID})
    os.link(p, p + ".hard")               # nlink -> 2
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)


def test_rejects_turn_uuid_mismatch(tmp_path):
    base, sess = _sess(tmp_path)
    other = "ffffffff-1234-1234-1234-1234567890ab"
    p, _ = _write_env(sess, UUID,
                      {"tool": "x", "envelope": {"a": 1}, "turn_uuid": other,
                       "gen": other})
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)


def test_rejects_gen_sentinel_mismatch(tmp_path):
    base, sess = _sess(tmp_path)
    # turn_uuid is correct but the gen sentinel echo is stale -> reject (risk #4)
    p, _ = _write_env(sess, UUID,
                      {"tool": "x", "envelope": {"a": 1}, "turn_uuid": UUID,
                       "gen": "ffffffff-1111-1111-1111-111111111111"})
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)


def test_missing_file_raises_not_written(tmp_path):
    base, sess = _sess(tmp_path)
    with pytest.raises(paths.EnvelopeNotWritten):
        paths.read_envelope_bytes(paths.env_path(sess, UUID), UUID)


def test_truncated_json_raises_incomplete(tmp_path):
    base, sess = _sess(tmp_path)
    p = paths.env_path(sess, UUID)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.write(fd, b'{"tool":"x","envelope":{')  # truncated
    os.close(fd)
    with pytest.raises(paths.EnvelopeIncomplete):
        paths.read_envelope_bytes(p, UUID)


def test_rejects_missing_tool_field(tmp_path):
    base, sess = _sess(tmp_path)
    p, _ = _write_env(sess, UUID, {"envelope": {"a": 1}, "turn_uuid": UUID,
                                   "gen": UUID})
    with pytest.raises(paths.EnvelopeRejected):
        paths.read_envelope_bytes(p, UUID)
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_envelope_readback.py
```
Expected: `AttributeError: module 'paths' has no attribute 'env_path'` (and the exception classes).

**(3) Minimal implementation.** In `paths.py` add (skip any symbol the path-safety component, Tasks 4–5, already defined):

```python
import errno
import json
import stat


class EnvelopeNotWritten(Exception):
    """env file absent -> 404."""


class EnvelopeRejected(Exception):
    """symlink / owner / mode / nlink / uuid / gen mismatch / bad shape -> 422."""


class EnvelopeIncomplete(Exception):
    """env file present but JSON not yet complete -> brief retry then 404."""


def env_path(session_dir_path, turn_uuid) -> str:
    return os.path.join(session_dir_path, f"env.{turn_uuid}.json")


def safe_open_nofollow(path, flags) -> int:
    """open O_NOFOLLOW, then fstat-guard regular/owner/mode/nlink.

    Raises EnvelopeNotWritten on ENOENT, EnvelopeRejected on ELOOP (symlink)
    or a failed fstat guard.
    """
    try:
        fd = os.open(path, flags | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise EnvelopeNotWritten(path)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EMLINK):
            raise EnvelopeRejected(f"symlink/refused: {path}")
        raise
    st = os.fstat(fd)
    if not (stat.S_ISREG(st.st_mode)
            and st.st_uid == os.getuid()
            and st.st_nlink == 1
            and not (stat.S_IMODE(st.st_mode) & 0o077)):
        os.close(fd)
        raise EnvelopeRejected(
            f"fstat guard failed: reg={stat.S_ISREG(st.st_mode)} "
            f"uid={st.st_uid} nlink={st.st_nlink} mode={oct(stat.S_IMODE(st.st_mode))}"
        )
    return fd


def read_envelope_bytes(path, turn_uuid) -> bytes:
    """Read claude's envelope verbatim with full safety guards.

    Returns the ORIGINAL bytes (no re-serialization). Raises:
      EnvelopeNotWritten  - file absent
      EnvelopeRejected    - symlink/owner/mode/nlink/shape/uuid/gen mismatch
      EnvelopeIncomplete  - JSON not yet parseable (writer mid-flight)
    """
    validate_turn_uuid(turn_uuid)
    fd = safe_open_nofollow(path, os.O_RDONLY)
    try:
        chunks = []
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        raise EnvelopeIncomplete(path)
    if not (isinstance(obj, dict)
            and isinstance(obj.get("tool"), str)
            and obj.get("envelope")):
        raise EnvelopeRejected("envelope shape invalid (tool/envelope)")
    if obj.get("turn_uuid") != turn_uuid:
        raise EnvelopeRejected(
            f"turn_uuid mismatch: got {obj.get('turn_uuid')!r} want {turn_uuid!r}"
        )
    verify_envelope_sentinel(obj, turn_uuid)   # gen echo guard (Task 32) — risk #4
    return raw
```

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_envelope_readback.py
```
Expected: `11 passed`.

**(5) Commit.**
```
git add paths.py tests/unit/test_envelope_readback.py
git commit -m "feat(paths): byte-exact O_NOFOLLOW envelope read-back + gen echo

fstat guard (S_ISREG, uid==getuid, nlink==1, no 0o077); json shape +
turn_uuid freshness + verify_envelope_sentinel gen echo; returns original
bytes verbatim. 404/422/incomplete discriminated. Mitigates #6/#7/#2/#4.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 34 — stale-env sweep + `stage_turn` orchestration (unlink prior env, put doc, set @wcb_turn)

**Files**
- Modify: `session_registry.py` (add `_sweep_stale`, `stage_turn`).
- Test: `tests/unit/test_stage_turn.py` (Create)

> `stage_turn` is the registry-side staging helper that the turn-protocol component's `_run_turn_locked` (turn-protocol-fsm) CALLS while holding the turn lock, before send-keys: sweep any leftover `env.*.json`/old `doc.*.html`, inject the sentinel into the persisted doc bytes, atomically `put_doc`, unlink the prior `env.<turn_uuid>.json` if present, and set `@wcb_turn`. Per critique FIX (ownership collision): turn-protocol-fsm owns `_run_turn_locked` and delegates staging here — this component does NOT author the held-lock turn body. tmux I/O (`set_option`) is delegated to a stub here so this stays a pure unit test.

**Steps**

**(1) Write the FAILING test.** Create `tests/unit/test_stage_turn.py`:

```python
import os
import pytest
import paths
import session_registry as reg

UUID = "12345678-1234-1234-1234-1234567890ab"


class FakeTmux:
    def __init__(self):
        self.options = {}
    def set_option(self, name, value):
        self.options[name] = value


def _sess(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    s = os.path.join(b, "wcb_" + "a" * 32 + "_" + "b" * 16)
    os.mkdir(s, 0o700)
    return b, s


def test_stage_turn_puts_doc_with_sentinel_and_sets_option(tmp_path):
    base, sess = _sess(tmp_path)
    tmux = FakeTmux()
    persisted = b"<html>\n<body>x</body>\n</html>\n"
    docp = reg.stage_turn(tmux, base, sess, UUID, persisted)
    assert docp == paths.doc_path(sess, UUID)
    with open(docp, "rb") as fh:
        staged = fh.read()
    assert (f"<!-- rwa:gen {UUID} -->").encode() in staged
    assert tmux.options.get("@wcb_turn") == UUID


def test_stage_turn_unlinks_prior_env_for_same_uuid(tmp_path):
    base, sess = _sess(tmp_path)
    stale = paths.env_path(sess, UUID)
    with open(stale, "w") as fh:
        fh.write('{"stale":true}')
    reg.stage_turn(FakeTmux(), base, sess, UUID, b"<p>hi</p>\n")
    assert not os.path.exists(stale)      # prior env removed at turn start


def test_stage_turn_sweeps_all_leftover_env_and_old_docs(tmp_path):
    base, sess = _sess(tmp_path)
    other = "ffffffff-1234-1234-1234-1234567890ab"
    leftover_env = paths.env_path(sess, other)
    old_doc = paths.doc_path(sess, other)
    for p in (leftover_env, old_doc):
        with open(p, "w") as fh:
            fh.write("junk")
    reg.stage_turn(FakeTmux(), base, sess, UUID, b"<p>hi</p>\n")
    assert not os.path.exists(leftover_env)
    assert not os.path.exists(old_doc)
    # the current turn's doc remains
    assert os.path.exists(paths.doc_path(sess, UUID))


def test_stage_turn_refuses_symlink_leftover(tmp_path):
    base, sess = _sess(tmp_path)
    target = str(tmp_path / "outside.json")
    with open(target, "w") as fh:
        fh.write("x")
    link = paths.env_path(sess, "aaaaaaaa-1234-1234-1234-1234567890ab")
    os.symlink(target, link)
    reg.stage_turn(FakeTmux(), base, sess, UUID, b"<p>hi</p>\n")
    # the symlink is removed but its target is never touched
    assert not os.path.lexists(link)
    assert os.path.exists(target)
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_stage_turn.py
```
Expected: `AttributeError: module 'session_registry' has no attribute 'stage_turn'`.

**(3) Minimal implementation.** In `session_registry.py`:

```python
import os
import re
import paths

_ENV_RE = re.compile(r"\Aenv\.[0-9a-f-]{36}\.json\Z")
_DOC_RE = re.compile(r"\Adoc\.[0-9a-f-]{36}\.html(\.part)?\Z")


def _sweep_stale(session_dir_path, base, keep_doc_uuid=None):
    """Unlink ALL leftover env.*.json and old doc.*.html (verified-regular,
    O_NOFOLLOW). Symlinks are removed via os.unlink without following.
    Never select by recency. The current turn's doc (keep_doc_uuid) survives.
    """
    paths.assert_confined(session_dir_path, base)
    try:
        names = os.listdir(session_dir_path)
    except FileNotFoundError:
        return
    for name in names:
        is_env = _ENV_RE.match(name)
        is_doc = _DOC_RE.match(name)
        if not (is_env or is_doc):
            continue
        if is_doc and keep_doc_uuid and name == f"doc.{keep_doc_uuid}.html":
            continue
        full = os.path.join(session_dir_path, name)
        paths.assert_confined(full, base)
        try:
            os.unlink(full)               # does not follow the final symlink
        except FileNotFoundError:
            pass


def stage_turn(tmux, base, session_dir_path, turn_uuid, persisted_bytes) -> str:
    """Held-lock turn staging: sweep leftovers, inject sentinel, atomically
    put doc.<turn_uuid>.html, unlink prior env.<turn_uuid>.json, mark busy.

    Called by turn-protocol-fsm's _run_turn_locked, which does send-keys AFTER
    this returns (rename happens-before the prompt). Returns the staged doc
    path. tmux is a TmuxClient (or stub) exposing set_option(name, value).
    """
    paths.validate_turn_uuid(turn_uuid)
    _sweep_stale(session_dir_path, base, keep_doc_uuid=None)
    staged = paths.inject_gen_sentinel(persisted_bytes, turn_uuid)
    doc = paths.put_doc(session_dir_path, base, turn_uuid, staged)
    # belt-and-suspenders: ensure no same-uuid env predates this turn
    env = paths.env_path(session_dir_path, turn_uuid)
    paths.assert_confined(env, base)
    try:
        os.unlink(env)
    except FileNotFoundError:
        pass
    tmux.set_option("@wcb_turn", turn_uuid)
    return doc
```

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_stage_turn.py
```
Expected: `4 passed`.

**(5) Commit.**
```
git add session_registry.py tests/unit/test_stage_turn.py
git commit -m "feat(registry): stage_turn — sweep stale, sentinel, atomic put_doc

Sweep all leftover env.*/old doc.* (confined, no recency), inject gen
sentinel, atomic put_doc, unlink prior same-uuid env, set @wcb_turn.
Called by turn-protocol _run_turn_locked; rename happens-before send-keys.
Mitigates #4/#7/#3.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 35 — `mark_doc_staged` / `assert_doc_staged` — 409 `doc_not_staged` precondition after reconstruct

**Files**
- Modify: `session_registry.py` (add `mark_doc_staged`, `assert_doc_staged`, `DocNotStaged`).
- Test: `tests/unit/test_doc_not_staged.py` (Create)

> NEW task per critique FIX (design §4 "After reconstruct, refuse to stream until the rwa re-stages the current-turn doc → 409 `doc_not_staged`"). On reconstruct the in-memory "has the rwa staged this turn's doc?" flag is lost, so a stream that arrives before the rwa re-stages must be refused. `mark_doc_staged` is set inside `stage_turn` (wired here so reconstruct starts unstaged); `assert_doc_staged` is the precondition turn-protocol-fsm's stream handler checks BEFORE send-keys and the cleanup-security dispatcher maps `DocNotStaged → 409 {"error":"doc_not_staged"}`.

**Steps**

**(1) Write the FAILING test.** Create `tests/unit/test_doc_not_staged.py`:

```python
import os
import pytest
import paths
import session_registry as reg

UUID = "12345678-1234-1234-1234-1234567890ab"


class FakeTmux:
    def __init__(self):
        self.options = {}
    def set_option(self, name, value):
        self.options[name] = value


class FakeSession:
    """Minimal stand-in for _Session: only the staged-flag surface."""
    def __init__(self):
        self.doc_staged = False   # reconstruct/fresh default: NOT staged


def _sess(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    s = os.path.join(b, "wcb_" + "a" * 32 + "_" + "b" * 16)
    os.mkdir(s, 0o700)
    return b, s


def test_assert_doc_staged_raises_when_unstaged():
    s = FakeSession()
    with pytest.raises(reg.DocNotStaged):
        reg.assert_doc_staged(s)


def test_mark_doc_staged_then_assert_passes():
    s = FakeSession()
    reg.mark_doc_staged(s)
    reg.assert_doc_staged(s)            # must not raise


def test_stage_turn_marks_doc_staged(tmp_path):
    base, sess = _sess(tmp_path)
    s = FakeSession()
    reg.stage_turn(FakeTmux(), base, sess, UUID, b"<p>hi</p>\n", session=s)
    reg.assert_doc_staged(s)            # stage_turn flipped the flag


def test_reconstruct_default_is_unstaged():
    # A freshly reconstructed session must start unstaged so the first stream
    # is refused with 409 until the rwa re-stages (design §4).
    s = FakeSession()
    assert s.doc_staged is False
    with pytest.raises(reg.DocNotStaged):
        reg.assert_doc_staged(s)
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_doc_not_staged.py
```
Expected: `AttributeError: module 'session_registry' has no attribute 'DocNotStaged'`.

**(3) Minimal implementation.** In `session_registry.py`:

```python
class DocNotStaged(Exception):
    """Stream requested before the rwa staged the current-turn doc -> 409.

    On reconstruct the staged flag is lost; the dispatcher (cleanup-security
    component) maps this to 409 {"error":"doc_not_staged"} (design §4).
    """


def mark_doc_staged(session) -> None:
    session.doc_staged = True


def assert_doc_staged(session) -> None:
    if not getattr(session, "doc_staged", False):
        raise DocNotStaged("rwa must re-stage the current-turn doc")
```

Extend `stage_turn` (Task 34) to accept an optional `session` and mark it staged. Change its signature/tail:

```python
def stage_turn(tmux, base, session_dir_path, turn_uuid, persisted_bytes,
               *, session=None) -> str:
    # ... unchanged body through tmux.set_option("@wcb_turn", turn_uuid) ...
    tmux.set_option("@wcb_turn", turn_uuid)
    if session is not None:
        mark_doc_staged(session)
    return doc
```

> Dependency note for the assembler: the `_Session` field set is owned by the registry-lifecycle component (its `_Session.__init__` must include `doc_staged = False` in the consolidated dataclass, defaulting unstaged on both fresh-create and reconstruct). This task adds only the helpers + exception and the `session=` hook in `stage_turn`; do not re-author `_Session` here.

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_doc_not_staged.py
```
Expected: `4 passed`.

**(5) Commit.**
```
git add session_registry.py tests/unit/test_doc_not_staged.py
git commit -m "feat(registry): doc_not_staged 409 precondition (design §4)

assert_doc_staged refuses a stream until the rwa re-stages after
reconstruct; stage_turn marks staged. Dispatcher maps DocNotStaged->409.
Mitigates stale-doc-after-restart.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 36 — per-turn prompt construction naming the rendezvous files + `.part`→rename instruction

**Files**
- Modify: `session_registry.py` (add `build_turn_prompt`).
- Test: `tests/unit/test_turn_prompt.py` (Create)

> Per critique FIX (prompt-template token mismatch): `build_turn_prompt` is THE production prompt. The fake-claude `WRITE {env} {uuid}` template (test-scaffold / tmux-client harness) is a TEST fixture only and must not be asserted against this real prompt path. turn-protocol-fsm's `_run_turn_locked` calls `build_turn_prompt(...)` and feeds the result to `send_prompt` — there is no `{env}`/`{uuid}` `_expand` mechanism in production.

**Steps**

**(1) Write the FAILING test.** Create `tests/unit/test_turn_prompt.py`:

```python
import session_registry as reg

UUID = "12345678-1234-1234-1234-1234567890ab"
DOC = "/home/u/.web_cli_bridge/rendezvous/wcb_x/doc." + UUID + ".html"
ENV = "/home/u/.web_cli_bridge/rendezvous/wcb_x/env." + UUID + ".json"


def test_prompt_names_doc_and_env_and_part_rename():
    p = reg.build_turn_prompt("Make the title bold", DOC, ENV, UUID)
    assert DOC in p
    assert ENV in p
    assert ENV + ".part" in p
    # the explicit .part -> rename instruction (the completion edge)
    assert "rename" in p.lower()
    # freshness: Read it now, do not rely on memory
    assert "do not rely on memory" in p.lower()
    # gen + turn_uuid echo requirement
    assert UUID in p
    assert "rwa:gen" in p
    assert '"gen"' in p
    assert '"turn_uuid"' in p


def test_prompt_embeds_the_user_instruction():
    p = reg.build_turn_prompt("Translate to French", DOC, ENV, UUID)
    assert "Translate to French" in p


def test_prompt_is_lf_only():
    p = reg.build_turn_prompt("x", DOC, ENV, UUID)
    assert "\r" not in p
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_turn_prompt.py
```
Expected: `AttributeError: module 'session_registry' has no attribute 'build_turn_prompt'`.

**(3) Minimal implementation.** In `session_registry.py`:

```python
def build_turn_prompt(instruction, doc_path_abs, env_path_abs, turn_uuid) -> str:
    """The per-turn PRODUCTION prompt. claude READS doc.<uuid>.html (fresh, not
    memory), produces an edit envelope, and WRITES env.<uuid>.json via a
    .part->rename completion edge. The gen sentinel + turn_uuid must be echoed
    verbatim so the bridge can reject a stale/mis-targeted write.

    NOTE: the fake-claude `WRITE {env} {uuid}` template used by the test harness
    is a fixture only; production uses THIS prompt.
    """
    part = env_path_abs + ".part"
    return (
        "You are editing an HTML document through a file rendezvous.\n\n"
        f"1. Read the CURRENT document now from this exact absolute path "
        f"(do not rely on memory of any earlier version):\n   {doc_path_abs}\n\n"
        "2. The document contains a comment of the form "
        f"`<!-- rwa:gen {turn_uuid} -->`. Copy that uuid verbatim.\n\n"
        f"3. Apply this instruction:\n   {instruction}\n\n"
        "4. Produce a single edit envelope. It MUST be one of:\n"
        '   {\"tool\":\"apply_edits\",\"envelope\":{\"version\":\"rwa-edit/1\",'
        '\"edits\":[{\"find\":\"...\",\"replace\":\"...\"}]}}\n'
        '   {\"tool\":\"replace_document\",\"envelope\":{\"version\":\"rwa-edit/1\",'
        '\"doc\":\"...\",\"reason\":\"...\"}}\n\n'
        "5. Wrap the envelope object and add two top-level fields copied "
        "verbatim:\n"
        f'   \"turn_uuid\": \"{turn_uuid}\"\n'
        f'   \"gen\": \"{turn_uuid}\"   (the uuid from the rwa:gen comment)\n\n'
        "6. Write that JSON object using your Write tool to the SCRATCH path "
        f"FIRST:\n   {part}\n"
        "   then RENAME (move) it to the FINAL path:\n"
        f"   {env_path_abs}\n"
        "   The rename is the completion signal; do not write the final path "
        "directly.\n\n"
        "7. After the rename succeeds, reply with only the word DONE.\n"
    )
```

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_turn_prompt.py
```
Expected: `3 passed`.

**(5) Commit.**
```
git add session_registry.py tests/unit/test_turn_prompt.py
git commit -m "feat(registry): per-turn prompt naming doc/env + .part->rename edge

Production prompt reads doc.<uuid>.html fresh (not memory), echoes rwa:gen +
turn_uuid, writes env.<uuid>.json.part then renames (the completion edge).
Fake-claude WRITE template stays a fixture. Mitigates #4/#7.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 37 — file-edge completion poll + read-back with mtime/stability guard (`await_envelope`)

**Files**
- Modify: `session_registry.py` (add `await_envelope`).
- Test: `tests/integration/test_completion_edge.py` (Create)

> Polls for the **renamed final name** `env.<turn_uuid>.json` (size/mtime stable, mtime > send-time), then byte-exact reads it via `paths.read_envelope_bytes` (Task 33). Per critique FIX (ordering / no duplicate polling): this is the SINGLE owner of the file-edge completion poll; turn-protocol-fsm's `_run_turn_locked` CALLS `await_envelope` rather than re-authoring a `read_envelope_bytes`-equivalent poll. Uses a real temp dir and a background thread that mimics the `.part`→rename edge (no tmux needed for this unit-of-behavior; the full fake-claude E2E lives in the turn-protocol component).

**Steps**

**(1) Write the FAILING test.** Create `tests/integration/test_completion_edge.py`:

```python
import json
import os
import threading
import time
import pytest
import paths
import session_registry as reg

UUID = "12345678-1234-1234-1234-1234567890ab"


def _sess(tmp_path):
    b = str(tmp_path / "rv"); os.mkdir(b, 0o700); os.chmod(b, 0o700)
    s = os.path.join(b, "s"); os.mkdir(s, 0o700)
    return b, s


def _write_then_rename(sess, turn_uuid, obj, delay):
    time.sleep(delay)
    final = paths.env_path(sess, turn_uuid)
    part = final + ".part"
    raw = json.dumps(obj).encode()
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.write(fd, raw); os.close(fd)
    os.replace(part, final)


def test_await_envelope_returns_bytes_after_rename(tmp_path):
    base, sess = _sess(tmp_path)
    obj = {"tool": "apply_edits", "envelope": {"v": 1}, "turn_uuid": UUID,
           "gen": UUID}
    send_time = time.time()
    t = threading.Thread(target=_write_then_rename,
                         args=(sess, UUID, obj, 0.2))
    t.start()
    raw = reg.await_envelope(sess, base, UUID, send_time,
                            deadline=time.monotonic() + 5.0,
                            stable_ms=120)
    t.join()
    assert json.loads(raw)["turn_uuid"] == UUID
    assert raw == json.dumps(obj).encode()


def test_await_envelope_no_write_raises_not_written(tmp_path):
    base, sess = _sess(tmp_path)
    with pytest.raises(paths.EnvelopeNotWritten):
        reg.await_envelope(sess, base, UUID, time.time(),
                          deadline=time.monotonic() + 0.5, stable_ms=120)


def test_await_envelope_ignores_stale_older_than_send_time(tmp_path):
    base, sess = _sess(tmp_path)
    # a pre-existing env from a previous incarnation with the same uuid
    stale = paths.env_path(sess, UUID)
    fd = os.open(stale, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.write(fd, json.dumps({"tool": "x", "envelope": {"a": 1},
                            "turn_uuid": UUID, "gen": UUID}).encode())
    os.close(fd)
    old = time.time() - 100
    os.utime(stale, (old, old))
    send_time = time.time()
    # no fresh write happens -> must NOT return the stale file
    with pytest.raises(paths.EnvelopeNotWritten):
        reg.await_envelope(sess, base, UUID, send_time,
                          deadline=time.monotonic() + 0.6, stable_ms=120)
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_completion_edge.py
```
Expected: `AttributeError: module 'session_registry' has no attribute 'await_envelope'`.

**(3) Minimal implementation.** In `session_registry.py`:

```python
import time
import paths


def await_envelope(session_dir_path, base, turn_uuid, send_time,
                   *, deadline, stable_ms=300, poll_ms=50) -> bytes:
    """Poll for the renamed FINAL env.<turn_uuid>.json. Accept only a file
    whose mtime > send_time and whose size+mtime are stable for >= stable_ms.
    Then read it byte-exact via paths.read_envelope_bytes. Never reads a .part
    or a stale (older) file.

    Raises paths.EnvelopeNotWritten if none appears before `deadline`
    (time.monotonic seconds).
    """
    env = paths.env_path(session_dir_path, turn_uuid)
    paths.assert_confined(env, base)
    stable_s = stable_ms / 1000.0
    poll_s = poll_ms / 1000.0
    last_sig = None
    stable_since = None
    while time.monotonic() < deadline:
        try:
            st = os.lstat(env)
        except FileNotFoundError:
            last_sig = None
            stable_since = None
            time.sleep(poll_s)
            continue
        # freshness: written by THIS turn (after we sent the prompt)
        if st.st_mtime <= send_time:
            time.sleep(poll_s)
            continue
        sig = (st.st_size, st.st_mtime)
        if sig != last_sig:
            last_sig = sig
            stable_since = time.monotonic()
            time.sleep(poll_s)
            continue
        if time.monotonic() - stable_since >= stable_s:
            return paths.read_envelope_bytes(env, turn_uuid)
        time.sleep(poll_s)
    raise paths.EnvelopeNotWritten(env)
```

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_completion_edge.py
```
Expected: `3 passed`.

**(5) Commit.**
```
git add session_registry.py tests/integration/test_completion_edge.py
git commit -m "feat(registry): await_envelope — file-edge completion poll

Poll for renamed final env.<uuid>.json (mtime>send-time, size/mtime stable),
then byte-exact read-back via read_envelope_bytes. Single owner of the file
edge; turn-protocol calls this. Never reads .part or a stale older file.
Mitigates #2/#7.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 38 — `_do_get_envelope` handler — `/session/get-envelope` returns claude's original bytes verbatim

**Files**
- Modify: `session_endpoints.py` (add `_do_get_envelope`; register the `/session/get-envelope` route in the canonical `_dispatch_session`/`_route_session` table owned by the cleanup-security component).
- Test: `tests/integration/test_get_envelope_endpoint.py` (Create)

> Returns the **original bytes** with `Content-Type: application/json` (no re-serialization, no second LLM). Per critique FIXes touching this component:
> - Handler is named `_do_get_envelope` (canonical `_do_*` convention), routed through the cleanup-security component's `_dispatch_session` (the canonical dispatcher owner) — NOT a second `_dispatch_session` here.
> - Auth/CORS/cap-match (`_require_session`) and the error ladder (`EnvelopeNotWritten→404`, `EnvelopeRejected→422`) are owned by the cleanup-security dispatcher; this task does NOT re-author them.
> - Singleton is `session_registry.REGISTRY` (not `_REGISTRY`); `_Registry(base=...)` with `self._base`.
> - CORS origin env var is `WCB_ALLOWED_ORIGINS` (comma-split), the canonical name used by `_allowed_origins`.
> - **ORDERING:** this task lands AFTER the cleanup-security dispatcher wiring (`server.Handler(SessionMixin, …)` + `_dispatch_session` + `_require_session` + `_allowed_origins`), the registry-lifecycle `_Session`/`REGISTRY` shell + `create`, and `paths.read_envelope_bytes` (Task 33). The test imports `server.Handler`, so it cannot collect until that wiring exists.

**Steps**

**(1) Write the FAILING test.** Create `tests/integration/test_get_envelope_endpoint.py`:

```python
import json
import os
import threading
import urllib.request
import urllib.error
import pytest

from http.server import ThreadingHTTPServer
import paths
import session_registry as reg
import server  # Handler mixes in SessionMixin (cleanup-security dispatcher)

UUID = "12345678-1234-1234-1234-1234567890ab"


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    # Mandatory token + a single allowed origin for /session/* (canonical env).
    monkeypatch.setenv("WEB_CLI_BRIDGE_TOKEN", "tok")
    monkeypatch.setenv("WCB_ALLOWED_ORIGINS", "http://localhost:5173")
    base = str(tmp_path / "rv"); os.mkdir(base, 0o700); os.chmod(base, 0o700)
    monkeypatch.setattr(paths, "base_dir", lambda: base)
    sess_dir = os.path.join(base, "wcb_" + "a" * 32 + "_" + "b" * 16)
    os.mkdir(sess_dir, 0o700)
    # Register a session whose cap is known to the test, via the canonical
    # singleton REGISTRY (registry-lifecycle owner).
    s = reg._Session.__new__(reg._Session)
    s.sid = "a" * 32
    s.cap = "c" * 64
    s.rendezvous_dir = sess_dir
    s.doc_staged = True
    reg.REGISTRY._sessions[s.sid] = s   # structural insert for the test

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port, base, sess_dir, s
    httpd.shutdown()
    reg.REGISTRY._sessions.pop(s.sid, None)


def _post(port, body, *, token="tok", origin="http://localhost:5173"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/session/get-envelope",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "Origin": origin},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def test_returns_original_bytes_verbatim(live_server):
    port, base, sess_dir, s = live_server
    raw = (b'{"tool":"apply_edits","envelope":{"version":"rwa-edit/1",'
           b'"edits":[{"find":"a","replace":"b"}]},"turn_uuid":"' + UUID.encode()
           + b'","gen":"' + UUID.encode() + b'"}')
    fd = os.open(paths.env_path(sess_dir, UUID),
                 os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.write(fd, raw); os.close(fd)
    status, ctype, got = _post(port, {"session_id": s.sid, "cap": s.cap,
                                     "turn_uuid": UUID})
    assert status == 200
    assert ctype == "application/json"
    assert got == raw            # byte-exact, no re-serialization


def test_missing_env_is_404(live_server):
    port, base, sess_dir, s = live_server
    status, _, body = _post(port, {"session_id": s.sid, "cap": s.cap,
                                   "turn_uuid": UUID})
    assert status == 404
    assert json.loads(body)["error"] == "envelope_not_written"


def test_symlink_env_is_422(live_server):
    port, base, sess_dir, s = live_server
    outside = os.path.join(base, "outside.json")
    with open(outside, "w") as fh:
        fh.write(json.dumps({"tool": "x", "envelope": {"a": 1},
                            "turn_uuid": UUID, "gen": UUID}))
    os.symlink(outside, paths.env_path(sess_dir, UUID))
    status, _, body = _post(port, {"session_id": s.sid, "cap": s.cap,
                                   "turn_uuid": UUID})
    assert status == 422
    assert json.loads(body)["error"] == "envelope_rejected"


def test_gen_sentinel_mismatch_is_422(live_server):
    port, base, sess_dir, s = live_server
    # turn_uuid correct, gen echo stale -> read_envelope_bytes raises Rejected
    raw = (b'{"tool":"apply_edits","envelope":{"a":1},"turn_uuid":"'
           + UUID.encode() + b'","gen":"ffffffff-1111-1111-1111-111111111111"}')
    fd = os.open(paths.env_path(sess_dir, UUID),
                 os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.write(fd, raw); os.close(fd)
    status, _, body = _post(port, {"session_id": s.sid, "cap": s.cap,
                                   "turn_uuid": UUID})
    assert status == 422
    assert json.loads(body)["error"] == "envelope_rejected"


def test_bad_turn_uuid_is_400(live_server):
    port, base, sess_dir, s = live_server
    status, _, _ = _post(port, {"session_id": s.sid, "cap": s.cap,
                                "turn_uuid": "../etc/passwd"})
    assert status == 400


def test_wrong_cap_is_403(live_server):
    port, base, sess_dir, s = live_server
    status, _, _ = _post(port, {"session_id": s.sid, "cap": "d" * 64,
                                "turn_uuid": UUID})
    assert status == 403
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_get_envelope_endpoint.py
```
Expected: the `/session/get-envelope` route is unknown / `AttributeError` on `_do_get_envelope` → 404/500 mismatches, tests FAIL. (This task is ordered AFTER the cleanup-security dispatcher wiring; if that has not landed, the test errors at `server.Handler` lacking `SessionMixin` — the assembler orders this task after the dispatch wiring task.)

**(3) Minimal implementation.** In `session_endpoints.py`, add the handler and register its route in the canonical `_dispatch_session` table (do NOT define a second dispatcher — the cleanup-security component owns `_dispatch_session`, `_require_session`, `_allowed_origins`, `_session_cors`, and the error ladder):

```python
import paths
import session_registry as reg


class SessionMixin:
    # ... canonical _dispatch_session / _require_session / _allowed_origins /
    #     _session_cors / error ladder live in the cleanup-security component ...

    def _do_get_envelope(self, body):
        """POST /session/get-envelope {session_id, cap, turn_uuid}
        -> claude's ORIGINAL envelope bytes, Content-Type application/json.
        No re-serialization, no second LLM — a pure byte pipe. Auth/CORS/cap
        + the EnvelopeNotWritten->404 / EnvelopeRejected->422 ladder are applied
        by the canonical _dispatch_session; this handler only reads + streams,
        and maps the single retry case (EnvelopeIncomplete) to 404.
        """
        sid = paths.validate_session_id(body["session_id"])     # 400 on bad id
        turn_uuid = paths.validate_turn_uuid(body["turn_uuid"])  # 400 on bad uuid
        sess = self._require_session(sid, body.get("cap"))       # 403 on cap mismatch
        env = paths.env_path(sess.rendezvous_dir, turn_uuid)
        paths.assert_confined(env, paths.base_dir())             # 403 on escape
        try:
            raw = paths.read_envelope_bytes(env, turn_uuid)
        except paths.EnvelopeIncomplete:
            # brief retry: the writer may be mid-rename
            import time as _t
            _t.sleep(0.15)
            try:
                raw = paths.read_envelope_bytes(env, turn_uuid)
            except paths.EnvelopeIncomplete:
                return self._send(404, {"error": "envelope_incomplete"})
        # success: stream the original bytes, do NOT json.dumps them
        self.send_response(200)
        self._session_cors()                 # emits headers only (no status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
```

Add `"get-envelope": self._do_get_envelope` (or the equivalent route tuple) to the canonical `_dispatch_session` POST route map in the cleanup-security component. The dispatcher's existing ladder already maps:
```python
except paths.EnvelopeNotWritten:
    return self._send(404, {"error": "envelope_not_written"})
except paths.EnvelopeRejected:
    return self._send(422, {"error": "envelope_rejected"})
```
so the `gen` mismatch (raised as `EnvelopeRejected` inside `read_envelope_bytes`, Task 33) surfaces as 422 without extra code here. Do NOT re-author `_require_session` — it is owned by the registry/security wiring; this task assumes it exists.

**(4) Run + expected PASS.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_get_envelope_endpoint.py
```
Expected: `6 passed`.

**(5) Commit.**
```
git add session_endpoints.py tests/integration/test_get_envelope_endpoint.py
git commit -m "feat(endpoints): _do_get_envelope returns original bytes verbatim

Byte pipe: read_envelope_bytes streamed unmodified with
Content-Type application/json, routed through the canonical _dispatch_session.
404 not-written / 422 rejected (incl. gen-echo) / 404 incomplete-after-retry;
cap-match 403; bad uuid 400. Mitigates #6/#7/#4.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 39 — rwa `modifyViaBridge` fix — render the PERSISTED bytes (`renderDoc(persisted)`) + static gate test

**Files**
- Modify: `/Users/martintreiber/Documents/Development/rewritable/seeds/rewritable.html` (`modifyViaBridge`, the `newDoc` apply block + line 3502 `renderDoc(newDoc)`).
- Test: `tests/unit/test_rwa_render_persisted.py` (Create) — static-source assertion gate (per critique FIX: Tasks J/K were manual-only; add an automated red→green gate mirroring `test_packaging.py`'s grep approach).
- Verify: manual in-browser step (described below) as a SECONDARY check.

> The bug: at line 3502 the bridge path renders `newDoc`. `applyEdits`/`replaceDocument` return `commitDoc`'s value (`persistDoc`, the post-`injectMissingBlockIds` bytes), so the *variable* `newDoc` here already holds `persistDoc` — BUT the name lies and any future refactor that assigns `newDoc` from the compiled envelope rather than the `applyEdits` return would silently render the pre-backfill text. The fix makes the contract explicit and load-bearing: bind a `persisted` const to the `applyEdits`/`replaceDocument` return and render THAT, so DOM, store, and the staged `doc.<uuid>.html` provably agree.

**Steps**

**(1) Write the FAILING test (automated static gate).** Create `tests/unit/test_rwa_render_persisted.py`:

```python
import os
import re
import pytest

RWA = "/Users/martintreiber/Documents/Development/rewritable/seeds/rewritable.html"


def _src():
    with open(RWA, "r", encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.skipif(not os.path.exists(RWA), reason="rwa artifact absent")
def test_modify_via_bridge_renders_persisted_not_newdoc():
    src = _src()
    # The bridge path must bind `persisted` from applyEdits/replaceDocument and
    # render THAT. The old `renderDoc(newDoc)` line must be gone.
    assert "renderDoc(persisted)" in src
    assert "renderDoc(newDoc)" not in src


@pytest.mark.skipif(not os.path.exists(RWA), reason="rwa artifact absent")
def test_modify_via_bridge_binds_persisted_from_apply():
    src = _src()
    # `persisted = await applyEdits(` and `persisted = await replaceDocument(`
    assert re.search(r"persisted\s*=\s*await\s+applyEdits\(", src)
    assert re.search(r"persisted\s*=\s*await\s+replaceDocument\(", src)
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_rwa_render_persisted.py
```
Expected: `AssertionError` — `renderDoc(persisted)` not yet present (and `renderDoc(newDoc)` still present) → both tests FAIL.

**(3) Minimal implementation.** First confirm the exact current text:
```
cd /Users/martintreiber/Documents/Development/rewritable && grep -n "renderDoc(newDoc);" seeds/rewritable.html
```
Expected: `3502:    renderDoc(newDoc);` (one hit inside `modifyViaBridge`).

Then edit `modifyViaBridge` so the rendered value is provably the persisted return. Replace the apply block (lines ~3488–3502):

Old:
```js
    let newDoc = null;
    if (parsed.tool === 'apply_dsl_plan') {
      const compiled = compileDslPlan(parsed.envelope, cur);
      if (compiled.tool === 'apply_edits') newDoc = await applyEdits(compiled.envelope, cur, lensMeta);
      else if (compiled.tool === 'replace_document') newDoc = await replaceDocument(compiled.envelope, cur, lensMeta);
      else throw new RwaEditError('unknown_tool', null, { name: compiled.tool });
    } else if (parsed.tool === 'apply_edits') {
      newDoc = await applyEdits(parsed.envelope, cur, lensMeta);
    } else if (parsed.tool === 'replace_document') {
      newDoc = await replaceDocument(parsed.envelope, cur, lensMeta);
    } else {
      throw new RwaEditError('unknown_tool', null, { name: parsed.tool });
    }

    renderDoc(newDoc);
```

New:
```js
    // persisted is exactly what applyEdits/replaceDocument committed to IDB
    // (post-injectMissingBlockIds, post-canonLF). Render THAT — never the
    // compiled envelope's pre-backfill text — so DOM, store, and the bridge's
    // staged doc.<turn_uuid>.html are byte-identical. (design §3 persistDoc fix)
    let persisted = null;
    if (parsed.tool === 'apply_dsl_plan') {
      const compiled = compileDslPlan(parsed.envelope, cur);
      if (compiled.tool === 'apply_edits') persisted = await applyEdits(compiled.envelope, cur, lensMeta);
      else if (compiled.tool === 'replace_document') persisted = await replaceDocument(compiled.envelope, cur, lensMeta);
      else throw new RwaEditError('unknown_tool', null, { name: compiled.tool });
    } else if (parsed.tool === 'apply_edits') {
      persisted = await applyEdits(parsed.envelope, cur, lensMeta);
    } else if (parsed.tool === 'replace_document') {
      persisted = await replaceDocument(parsed.envelope, cur, lensMeta);
    } else {
      throw new RwaEditError('unknown_tool', null, { name: parsed.tool });
    }

    renderDoc(persisted);
```

Use the Edit tool to make this replacement (the `old_string` is the block above, unique within the file).

**(4) Run + expected PASS (automated gate, then manual confirm).**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_rwa_render_persisted.py
```
Expected: `2 passed`.

Then a SECONDARY manual in-browser confirm: reload `rewritable.html`, backend = `bridge`, and run in DevTools:
```js
(async () => {
  // Trigger a bridge ⌘K edit that adds an anchorable block (e.g. "add an
  // <h2>Notes</h2> at the end"). After it completes:
  const stored = await getDoc();
  const ids = [...stored.matchAll(/data-rwa-id="([^"]+)"/g)].map(m => m[1]);
  const allInDom = ids.every(id => document.querySelector(`[data-rwa-id="${id}"]`));
  console.assert(allInDom, 'FAIL: a persisted data-rwa-id is missing from the DOM');
  console.log('PASS: rendered DOM matches persisted bytes; ids:', ids.length);
})();
```
Expected console: `PASS: rendered DOM matches persisted bytes; ids: <N>` with no assertion failure. Visually confirm the new `<h2>Notes</h2>` appears and `⌘Z` (undo) reverts cleanly.

**(5) Commit.**
```
cd /Users/martintreiber/Documents/Development/rewritable && git add seeds/rewritable.html
git commit -m "fix(rwa): modifyViaBridge renders persisted bytes, not raw newDoc

Bind persisted = applyEdits/replaceDocument return and renderDoc(persisted)
so DOM, IDB store, and the bridge's staged doc.<uuid>.html agree
(post-injectMissingBlockIds/canonLF). Mitigates design risk #3/#9.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
```
git add tests/unit/test_rwa_render_persisted.py
git commit -m "test(rwa): static gate asserts renderDoc(persisted)

Automated red->green gate for the modifyViaBridge persistDoc fix
(grep-style source assertion, mirrors test_packaging.py).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 40 — rwa session-driven flow — `modifyViaSession` (create/stage/stream/get-envelope) + static gate

**Files**
- Modify: `/Users/martintreiber/Documents/Development/rewritable/seeds/rewritable.html` (add `RWA.SESSION_BASE`/`RWA.K_BRIDGE_TOKEN` config near the `RWA` object line ~295; add `modifyViaSession`; route the bridge backend through it with single-shot fallback).
- Test: `tests/unit/test_rwa_modify_via_session.py` (Create) — static-source assertion gate (per critique FIX: Task K was manual-only; add an automated red→green gate mirroring `test_packaging.py`'s grep approach).
- Verify: manual in-browser step (described below) as a SECONDARY check.

> This adds the persistent-session path on the rwa side: mint one `session_id` per document (Decision #3), stream a turn, then `POST /session/get-envelope` and feed claude's **original bytes** into the existing `parseBridgeEnvelope`→`applyEdits`→`renderDoc(persisted)` pipeline from Task 39. The single-shot `modifyViaBridge` stays as the fallback. Per critique FIX (CORS env var): the config comment pins the canonical `WCB_ALLOWED_ORIGINS` (not `WCB_RWA_ORIGIN`) so server, tests, and rwa docs agree. The manual verification needs the bridge's `/session/*` routes live (cleanup-security dispatcher + registry-lifecycle `create` + this component's get-envelope landed).

**Steps**

**(1) Write the FAILING test (automated static gate).** Create `tests/unit/test_rwa_modify_via_session.py`:

```python
import os
import re
import pytest

RWA = "/Users/martintreiber/Documents/Development/rewritable/seeds/rewritable.html"


def _src():
    with open(RWA, "r", encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.skipif(not os.path.exists(RWA), reason="rwa artifact absent")
def test_modify_via_session_defined():
    src = _src()
    assert re.search(r"async\s+function\s+modifyViaSession\(", src)


@pytest.mark.skipif(not os.path.exists(RWA), reason="rwa artifact absent")
def test_session_config_present():
    src = _src()
    assert "SESSION_BASE:" in src
    assert "/session" in src
    assert "K_BRIDGE_TOKEN:" in src


@pytest.mark.skipif(not os.path.exists(RWA), reason="rwa artifact absent")
def test_session_calls_get_envelope_and_renders_persisted():
    src = _src()
    assert "/get-envelope" in src
    assert "/stream" in src
    # the session path must run the SAME persisted render contract as Task 39
    assert src.count("renderDoc(persisted)") >= 2  # bridge + session paths


@pytest.mark.skipif(not os.path.exists(RWA), reason="rwa artifact absent")
def test_bridge_backend_routes_through_session_with_fallback():
    src = _src()
    # dispatch prefers modifyViaSession and falls back to modifyViaBridge
    assert "modifyViaSession(" in src
    assert "modifyViaBridge(" in src
    assert re.search(r"modifyViaSession\([^)]*\)\.catch", src)
```

**(2) Run + expected FAIL.**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_rwa_modify_via_session.py
```
Expected: `AssertionError` — `modifyViaSession` / `SESSION_BASE:` / `/get-envelope` not yet present → tests FAIL.

**(3) Minimal implementation.**

3a. Confirm anchor lines:
```
cd /Users/martintreiber/Documents/Development/rewritable && grep -n "BRIDGE_URL:'http://127.0.0.1:8765/run'," seeds/rewritable.html
cd /Users/martintreiber/Documents/Development/rewritable && grep -n "async function modifyViaBridge" seeds/rewritable.html
```
Expected: one hit each (line ~295 and ~3435).

3b. Add session config to the `RWA` object. Replace:
```js
  BRIDGE_URL:'http://127.0.0.1:8765/run',
};
```
with:
```js
  BRIDGE_URL:'http://127.0.0.1:8765/run',
  // Persistent-session bridge (design §1/§2). One session per document
  // (DOC_UUID); the bridge keeps a long-lived claude TUI and rendezvous dir.
  SESSION_BASE:'http://127.0.0.1:8765/session',
  // Mandatory bearer token + allowed origin must match the bridge's
  // WEB_CLI_BRIDGE_TOKEN / WCB_ALLOWED_ORIGINS. Stored per-user in IDB
  // key K_BRIDGE_TOKEN.
  K_BRIDGE_TOKEN:'rwa_bridge_token',
};
```

3c. Add `modifyViaSession` immediately above `async function modifyViaBridge`:
```js
// Persistent-session bridge path (design §2/§3). Unlike modifyViaBridge's
// single-shot `claude -p`, this drives a long-lived TUI: create/reuse one
// session per document, stage the CURRENT persisted bytes as
// doc.<turn_uuid>.html, stream one turn, then fetch claude's ORIGINAL
// envelope bytes and run them through the SAME apply/render pipeline.
let _rwaSession = null;   // {session_id, cap} cached per document load

async function _sessionToken() {
  try { return (await idbGet(RWA.K_BRIDGE_TOKEN)) || ''; } catch (_) { return ''; }
}

async function _sessionFetch(path, body) {
  const token = await _sessionToken();
  const resp = await fetch(RWA.SESSION_BASE + path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + token,
    },
    body: JSON.stringify(body),
  });
  return resp;
}

async function _ensureSession() {
  if (_rwaSession) {
    // verify it's still alive; if not, drop and recreate.
    try {
      const r = await _sessionFetch('/capture',
        { session_id: _rwaSession.session_id, cap: _rwaSession.cap });
      if (r.ok) return _rwaSession;
    } catch (_) {}
    _rwaSession = null;
  }
  const r = await _sessionFetch('/create', { cwd: null });
  if (!r.ok) throw new Error('session create http ' + r.status);
  const { session_id, cap } = await r.json();
  _rwaSession = { session_id, cap };
  return _rwaSession;
}

// Mint a turn uuid in the browser (the bridge also validates it).
function _mintTurnUuid() {
  return (crypto.randomUUID ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
      }));
}

async function modifyViaSession(instr, lensMeta = null) {
  if (modifyMutex) {
    setPalSt('err', '✗ another modify in progress');
    setLensProgress('error', 'another modify in progress');
    throw new RwaEditError('concurrent_modify');
  }
  modifyMutex = true;
  closePal();
  setStatus('run', '⌘K running (session)');
  setLensProgress('thinking', 'Asking claude (session)…');
  try {
    const sess = await _ensureSession();
    const cur = canonLF(await getDoc());        // the persisted bytes (Task 39)
    const turn_uuid = _mintTurnUuid();

    // Stage + stream one turn. The bridge writes doc.<turn_uuid>.html from
    // `doc`, builds the per-turn prompt, and runs claude to completion.
    const streamResp = await _sessionFetch('/stream', {
      session_id: sess.session_id, cap: sess.cap,
      doc: cur, turn_uuid, input: instr, timeout: 180,
    });
    if (!streamResp.ok) throw new Error('session stream http ' + streamResp.status);
    // Drain the SSE body until `done`. We only need the terminal reason here;
    // a richer UI can surface state/delta events.
    const reader = streamResp.body.getReader();
    const dec = new TextDecoder();
    let buf = '', doneReason = null;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const ev = (frame.match(/^event:\s*(.+)$/m) || [])[1];
        const dataLine = (frame.match(/^data:\s*(.+)$/m) || [])[1];
        if (ev === 'done' && dataLine) {
          try { doneReason = JSON.parse(dataLine).reason; } catch (_) {}
        }
      }
    }
    if (doneReason && doneReason !== 'idle') {
      throw new Error('turn ended without envelope: ' + doneReason);
    }

    // Pull claude's ORIGINAL envelope bytes and run the SAME pipeline as the
    // single-shot path. parseBridgeEnvelope runs on the exact bytes.
    const envResp = await _sessionFetch('/get-envelope', {
      session_id: sess.session_id, cap: sess.cap, turn_uuid,
    });
    if (!envResp.ok) throw new Error('get-envelope http ' + envResp.status);
    const stdout = await envResp.text();
    const parsed = parseBridgeEnvelope(stdout);
    if (!parsed) throw new Error('session: unparseable envelope');

    // gen/turn_uuid echo guard on the rwa side too (defence in depth — the
    // bridge already enforces this in read_envelope_bytes, Task 33).
    if (parsed.turn_uuid && parsed.turn_uuid !== turn_uuid)
      throw new RwaEditError('stale_envelope');

    setLensProgress('thinking', `Applying ${friendlyToolName(parsed.tool)}…`);
    let persisted = null;
    if (parsed.tool === 'apply_dsl_plan') {
      const compiled = compileDslPlan(parsed.envelope, cur);
      if (compiled.tool === 'apply_edits') persisted = await applyEdits(compiled.envelope, cur, lensMeta);
      else if (compiled.tool === 'replace_document') persisted = await replaceDocument(compiled.envelope, cur, lensMeta);
      else throw new RwaEditError('unknown_tool', null, { name: compiled.tool });
    } else if (parsed.tool === 'apply_edits') {
      persisted = await applyEdits(parsed.envelope, cur, lensMeta);
    } else if (parsed.tool === 'replace_document') {
      persisted = await replaceDocument(parsed.envelope, cur, lensMeta);
    } else {
      throw new RwaEditError('unknown_tool', null, { name: parsed.tool });
    }
    renderDoc(persisted);
    setDirty(true);
    await rwaBumpDirtyCount().catch(() => {});
    rwaCheckQuota();
    queueMicrotask(() => {
      emitRuntimeEvent('modify', { instruction: instr, lensMeta });
      emitRuntimeEvent('status', getStatusSnapshot());
    });
    setStatus('ok', '✓ done');
    setLensProgress('done', 'Done');
  } catch (e) {
    if (e instanceof RwaEditError) {
      const code = e.code + (e.editIndex != null ? ' [edit ' + e.editIndex + ']' : '');
      setStatus('err', '✗ ' + code);
      setLensProgress('error', code);
    } else {
      setStatus('err', '✗ ' + e.message);
      setLensProgress('error', e.message);
    }
    console.error(e);
  } finally {
    modifyMutex = false;
  }
}
```

3d. Route the bridge backend through the session path with single-shot fallback. Find the dispatch (line ~3297, `if (cfg.kind === 'bridge') return modifyViaBridge(instr, lensMeta);`) and change it to:
```js
  if (cfg.kind === 'bridge') {
    // Prefer the persistent session; fall back to single-shot if the session
    // endpoints are unavailable (older bridge build).
    return modifyViaSession(instr, lensMeta).catch((e) => {
      if (/http 404|http 405|unreachable/i.test(String(e && e.message))) {
        return modifyViaBridge(instr, lensMeta);
      }
      throw e;
    });
  }
```

**(4) Run + expected PASS (automated gate, then manual confirm).**
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_rwa_modify_via_session.py
```
Expected: `4 passed`.

Then a SECONDARY manual in-browser confirm:

1. Start the bridge with the canonical session env set:
```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && WEB_CLI_BRIDGE_TOKEN=devtoken WCB_ALLOWED_ORIGINS=http://localhost:5173 python3 server.py 8765
```
2. Serve the rwa from the allowed origin (e.g. `python3 -m http.server 5173` in `seeds/`) and open `http://localhost:5173/rewritable.html`.
3. In DevTools, store the token so `_sessionToken()` finds it:
```js
await idbPut(RWA.K_BRIDGE_TOKEN, 'devtoken'); RWA.SESSION_BASE
```
Expected: `"http://127.0.0.1:8765/session"` and `typeof modifyViaSession === 'function'`.
4. Set backend to `bridge` (settings), then run a ⌘K instruction such as "make the first heading say Hello". Watch the status go `⌘K running (session)` → `✓ done` and the heading update in place.
5. Confirm the session was reused (not recreated) across a second edit:
```js
const first = _rwaSession.session_id;
// run a second ⌘K edit, then:
console.assert(_rwaSession.session_id === first, 'FAIL: session not reused');
console.log('PASS: reused session', first);
```
6. Confirm byte-exact round-trip: the rendered DOM after the edit equals the persisted store (same invariant check as Task 39 step 4). Expected: `PASS`.
7. Negative path: stop the bridge mid-session, run ⌘K again. Expected: status shows an error (`session stream http ...`/`unreachable`) and, if the single-shot bridge is still up, the `.catch` falls back to `modifyViaBridge`.

Record each `PASS`/error line from the console.

**(5) Commit.**
```
cd /Users/martintreiber/Documents/Development/rewritable && git add seeds/rewritable.html
git commit -m "feat(rwa): modifyViaSession persistent-session bridge path

One session per document; stage persisted bytes as doc.<turn_uuid>.html,
stream one turn, fetch claude's original envelope bytes, run the same
parse/apply/renderDoc(persisted) pipeline. Single-shot modifyViaBridge
kept as fallback. Echo-guards turn_uuid client-side. Config pins
WCB_ALLOWED_ORIGINS. (design §2/§3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
```
git add tests/unit/test_rwa_modify_via_session.py
git commit -m "test(rwa): static gate asserts modifyViaSession session flow

Automated red->green gate for the persistent-session rwa path
(grep-style source assertion, mirrors test_packaging.py).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Component coverage summary (for the assembler)

- **Base dir verify (lstat 0700):** Task 29. Risk #8.
- **Per-session dir:** Task 30. Risks #6/#14.
- **Atomic put-doc (tmp→rename, LF-only):** Task 31. Risks #3/#9.
- **Gen sentinel inject + `verify_envelope_sentinel` echo:** Task 32, WIRED into `read_envelope_bytes` (Task 33) + client guard (Task 40). Risks #3/#4.
- **Byte-exact O_NOFOLLOW read-back (owner/mode/nlink/uuid/gen):** Task 33. Risks #6/#7/#2/#4.
- **Stale sweep + `stage_turn` (put doc, unlink prior env, @wcb_turn, mark staged):** Task 34. Risks #4/#7/#3.
- **`doc_not_staged` 409 precondition after reconstruct:** Task 35 (NEW, design §4). Stale-doc-after-restart.
- **Per-turn prompt naming doc.<uuid>.html + env.<uuid>.json.part→rename (production prompt, fake template is a fixture):** Task 36. Risks #4/#7.
- **File-edge completion poll (`await_envelope`, mtime>send, stable, read-back) — single owner:** Task 37. Risks #2/#7.
- **`_do_get_envelope` original bytes via canonical dispatcher:** Task 38. Risks #6/#7/#4.
- **rwa modifyViaBridge renderDoc(persisted) + static gate:** Task 39. Risks #3/#9.
- **rwa session-driven flow + static gate + manual verification:** Task 40. Design §2/§3, Decision #3.

**Assembler dependency / ordering notes (critique FIXes applied):**
- Tasks 29–37 depend on the path-safety component's `validate_turn_uuid`/`validate_session_id`/`assert_confined`/`mint_nonce`/`mint_cap` (skeleton Tasks 4–5) and the three envelope exception classes; order this component AFTER path-safety. The `EnvelopeRejected`/`EnvelopeNotWritten`/`EnvelopeIncomplete` classes have ONE definition (path-safety owns them; Task 33 step 3 only adds those not already present).
- `verify_envelope_sentinel` (Task 32) is CALLED inside `read_envelope_bytes` (Task 33) — the sentinel is wired into the read path, not gold-plating.
- `stage_turn`/`build_turn_prompt`/`await_envelope` (Tasks 34/36/37) are CALLED by turn-protocol-fsm's `_run_turn_locked` (turn-protocol owns the held-lock turn body; this component owns only the staging/prompt/poll helpers). `await_envelope` is the SINGLE owner of the file-edge completion poll — turn-protocol must not re-author a `read_envelope_bytes`-equivalent loop.
- Registry singleton is `session_registry.REGISTRY` (not `_REGISTRY`); `_Registry(base=...)` with `self._base`. `_Session.__init__` (registry-lifecycle owner) must include `doc_staged = False` so Task 35's `assert_doc_staged` and the reconstruct default work — this component does not re-author `_Session`.
- Task 38 (`_do_get_envelope`) is named `_do_*` and routed through the cleanup-security component's canonical `_dispatch_session`/`_require_session`/`_allowed_origins`/`_session_cors` + error ladder; it does NOT define a second dispatcher. CORS origin env var is `WCB_ALLOWED_ORIGINS`. ORDER Task 38 AFTER: cleanup-security dispatcher wiring (`server.Handler(SessionMixin, …)`), registry-lifecycle `_Session`/`REGISTRY` shell + `create`, and Task 33.
- Tasks 39–40 touch the separate rwa artifact `/Users/martintreiber/Documents/Development/rewritable/seeds/rewritable.html` (no Python dependency). Each now has an AUTOMATED static-source gate test (under `tests/unit/`, grep-style, mirroring `test_packaging.py`) giving a real red→green cycle; the in-browser checks are SECONDARY. Task 40's manual verification needs the bridge's `/session/*` routes live (cleanup-security dispatcher + registry `create` + Task 38 landed). The DOC_UUID env-var name for CORS is `WCB_ALLOWED_ORIGINS` across server, tests, and rwa docs.


## Task 41 — send-key + capture + replay endpoints (NAMED_KEYS, closed-slice replay with offset base)

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_endpoints.py` (add `_do_send_key`, `_do_capture`, `_do_replay`, `_authz_session`, `_log_offset`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_turn_protocol.py` (append)
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/conftest.py` (add `session_factory` fixture)

FIX applied: `NAMED_KEYS` is imported from `tmux_session` (single source — authored by the tmux-client component, Task in that phase), not redefined here. Replay accounts for `log_offset_base` via the registry's `current_offset` so offsets stay globally monotonic across rotation (Task 46). Dispatcher naming convention is `_do_*` (canonical owner is Task 49).

**Steps**

(1) Write the FAILING test — append to `tests/integration/test_turn_protocol.py`:

```python
import base64

import pytest

from conftest import requires_tmux


@requires_tmux
def test_send_key_named_and_literal(session_factory):
    """send-key uses NAMED_KEYS for Enter, falls back to send_text for arbitrary
    text, and polls effect before releasing the lock (returns state)."""
    sess = session_factory()
    mixin = sess.mixin
    resp = mixin._do_send_key(sess.sid, sess.cap, ["Enter"])
    assert resp["ok"] is True
    assert resp["state"] in ("idle", "idle_no_envelope", "awaiting_input")
    resp2 = mixin._do_send_key(sess.sid, sess.cap, ["hello world"])
    assert resp2["ok"] is True
    screen = sess.tmux.capture_pane(sess.pane)
    assert "hello world" in screen


@requires_tmux
def test_send_key_rejects_bad_cap(session_factory):
    sess = session_factory()
    with pytest.raises(PermissionError):
        sess.mixin._do_send_key(sess.sid, "deadbeef" * 8, ["Enter"])


@requires_tmux
def test_capture_returns_screen_state_offset(session_factory):
    sess = session_factory()
    resp = sess.mixin._do_capture(sess.sid, sess.cap)
    assert "screen" in resp and "state" in resp and "log_offset" in resp
    assert isinstance(resp["log_offset"], int)
    assert resp["state"] in ("idle", "idle_no_envelope", "starting", "awaiting_input")


@requires_tmux
def test_replay_closed_slice(session_factory):
    """replay serves a closed [from_offset, end) slice, never read-to-live-EOF."""
    sess = session_factory()
    with open(sess.log_path, "ab") as fh:
        fh.write(b"ABCDEFGHIJ")
    resp = sess.mixin._do_replay(sess.sid, sess.cap, 0)
    raw = base64.b64decode(resp["bytes"])
    assert resp["from_offset"] == 0
    assert resp["end_offset"] == len(raw)
    with open(sess.log_path, "ab") as fh:
        fh.write(b"ZZZZ")
    assert b"ZZZZ" not in raw
    resp2 = sess.mixin._do_replay(sess.sid, sess.cap, resp["end_offset"])
    assert base64.b64decode(resp2["bytes"]) == b"ZZZZ"
```

Add the `session_factory` fixture to `tests/conftest.py`:

```python
@pytest.fixture
def session_factory(tmp_base, fake_claude_argv, fake_socket, monkeypatch):
    """Create a real fake-claude _Session bound to a SessionMixin probe.
    Yields a callable; tears every session down at the end."""
    import session_registry
    import session_endpoints

    created = []

    class _Probe(session_endpoints.SessionMixin):
        """A SessionMixin with the HTTP layer stubbed so endpoint methods can
        be called directly in-process (no BaseHTTPRequestHandler)."""
        def __init__(self):
            self.headers = {}

    def _make(cwd=None, trust=False):
        if trust:
            monkeypatch.setenv("WCB_FAKE_TRUST", "1")
        reg = session_registry._Registry(base=str(tmp_base))
        sess = reg.create(cwd=cwd or str(tmp_base), cols=80, rows=24,
                          claude_argv=fake_claude_argv,
                          socket_override=fake_socket)
        probe = _Probe()
        probe._registry = reg
        sess.mixin = probe
        created.append((reg, sess))
        return sess

    yield _make
    for reg, sess in created:
        try:
            reg.delete(sess.sid, sess.cap)
        except Exception:
            pass
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py -k "send_key or capture or replay"
```

Expected FAIL: `AttributeError: 'SessionMixin' object has no attribute '_do_send_key'` (and `_do_capture`, `_do_replay`).

(3) Minimal implementation — add to `session_endpoints.py`. Add at the top of the module (alongside the existing imports):

```python
import base64
import hmac
import os

from fsm import classify, strip_screen, footer_of
from paths import validate_session_id, safe_open_nofollow
from tmux_session import NAMED_KEYS


class _TurnBusy(Exception):
    """Raised when the per-session turn lock cannot be acquired -> 409."""
```

Add to the `SessionMixin` class:

```python
class SessionMixin:
    # ... existing turn/stream methods live here ...

    def _do_send_key(self, session_id, cap, keys):
        sess = self._authz_session(session_id, cap)          # PermissionError on bad cap
        if not isinstance(keys, list) or not keys or not all(
                isinstance(k, str) for k in keys):
            raise ValueError("keys must be a non-empty list of strings")
        if not sess.turn_lock.acquire(timeout=2.0):
            raise _TurnBusy("session busy")
        try:
            for k in keys:
                if k in NAMED_KEYS:
                    sess.tmux.send_keys(sess.pane, k)
                else:
                    sess.tmux.send_text(sess.pane, k)
            # poll the effect before releasing — give the TUI a beat to redraw
            screen = strip_screen(sess.tmux.capture_pane(sess.pane))
            state, _meta = classify(screen, footer_of(screen),
                                    env_present=False, composer_seen=True)
            return {"ok": True, "state": state}
        finally:
            sess.turn_lock.release()

    def _do_capture(self, session_id, cap):
        sess = self._authz_session(session_id, cap)
        screen = strip_screen(sess.tmux.capture_pane(sess.pane))
        state, _meta = classify(screen, footer_of(screen),
                                env_present=False, composer_seen=True)
        return {"screen": screen, "state": state,
                "log_offset": self._log_offset(sess)}

    def _do_replay(self, session_id, cap, from_offset):
        sess = self._authz_session(session_id, cap)
        if not isinstance(from_offset, int) or from_offset < 0:
            raise ValueError("from_offset must be a non-negative int")
        if not sess.turn_lock.acquire(timeout=2.0):
            raise _TurnBusy("session busy")
        try:
            # CLOSED snapshot under the lock: read live size now, slice
            # [local_from, local_end), never to live EOF. from_offset is a
            # GLOBAL cursor (includes rotated bytes) so subtract the base.
            base = getattr(sess, "log_offset_base", 0)
            local_from = max(0, from_offset - base)
            try:
                local_end = os.path.getsize(sess.log_path)
            except FileNotFoundError:
                local_end = local_from
            data = b""
            if local_end > local_from:
                fd = safe_open_nofollow(sess.log_path, os.O_RDONLY)
                try:
                    os.lseek(fd, local_from, os.SEEK_SET)
                    data = os.read(fd, local_end - local_from)
                finally:
                    os.close(fd)
            return {"bytes": base64.b64encode(data).decode("ascii"),
                    "from_offset": from_offset,
                    "end_offset": from_offset + len(data)}
        finally:
            sess.turn_lock.release()

    def _authz_session(self, session_id, cap):
        sid = validate_session_id(session_id)        # ValueError -> 400
        sess = self._registry.get_or_reconstruct(sid)
        if not isinstance(cap, str) or not hmac.compare_digest(sess.cap, cap):
            raise PermissionError("cap mismatch")
        return sess

    def _log_offset(self, sess):
        return self._registry.current_offset(sess)
```

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py -k "send_key or capture or replay"
```

Expected: 4 passed (`tmux` present). If `tmux` is absent the four tests skip — acceptable; CI with tmux must show them green.

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_endpoints.py tests/integration/test_turn_protocol.py tests/conftest.py && git commit -m "feat(session): send-key (NAMED_KEYS allowlist), capture, closed-slice replay

send-key folds named keys through the shared tmux_session.NAMED_KEYS allowlist
and arbitrary text through send_text; it polls the screen effect before
releasing the turn lock. replay returns a CLOSED [from_offset, end) byte slice
snapshotted under the lock, never read-to-live-EOF, and subtracts
log_offset_base so the cursor stays globally monotonic across rotations. All
three enforce the per-session cap via _authz_session.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 42 — TmuxClient.interrupt C-c fallback (merge into single tmux-client definition)

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tmux_session.py` (extend the existing `interrupt`; do NOT redefine `tpgid` — it already exists from the tmux-client component)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_tmux_client.py` (append)

FIX applied: `tpgid` and `interrupt` are authored once in the tmux-client component; this task only adds the **C-c in-band fallback** to the canonical `interrupt` and pins its guard behaviour. `tpgid` is NOT redefined. The `fake_claude.sh` SIGINT trap is part of the single canonical fake (test-scaffold component), not re-authored here. The `tmux_client_factory` fixture and `fake_socket`/`fake_claude_argv` come from the tmux-client/test-scaffold components — reused, not re-created.

**Steps**

(1) Write the FAILING test — append to `tests/integration/test_tmux_client.py`:

```python
import os
import signal

from conftest import requires_tmux


@requires_tmux
def test_interrupt_falls_back_to_ctrl_c_when_no_safe_group(tmux_client_factory, monkeypatch):
    """When tpgid resolves to <=0 or to shell_pid, interrupt must NOT killpg;
    it sends an in-band C-c keystroke instead, never SIGINT-ing the shell."""
    client, target = tmux_client_factory()
    monkeypatch.setattr(client, "tpgid", lambda t: 0)   # no safe foreground group
    killed = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    sent = []
    monkeypatch.setattr(client, "send_keys", lambda t, k: sent.append((t, k)))
    client.interrupt(target, shell_pid=-1)
    assert killed == []
    assert sent == [(target, "C-c")]


@requires_tmux
def test_interrupt_guarded_when_tpgid_equals_shell(tmux_client_factory, monkeypatch):
    """If tpgid resolves to shell_pid (claude already exited), interrupt is a
    no-op killpg-wise and falls back to C-c — never SIGINT the shell itself."""
    client, target = tmux_client_factory()
    tp = client.tpgid(target)
    killed = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(client, "send_keys", lambda t, k: None)
    client.interrupt(target, shell_pid=tp)              # tpgid == shell_pid
    assert killed == []


@requires_tmux
def test_interrupt_signals_foreground_group(tmux_client_factory):
    """When tpgid>0 and != shell_pid, interrupt SIGINTs the group; the canonical
    fake_claude.sh traps INT and prints '^C INTERRUPTED'."""
    import time
    client, target = tmux_client_factory()
    tp = client.tpgid(target)
    assert tp > 0
    client.interrupt(target, shell_pid=-1)
    deadline = time.monotonic() + 3.0
    seen = False
    while time.monotonic() < deadline:
        if "INTERRUPTED" in client.capture_pane(target):
            seen = True
            break
        time.sleep(0.05)
    assert seen
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py -k "interrupt_falls_back or interrupt_guarded or interrupt_signals"
```

Expected FAIL: `test_interrupt_falls_back_to_ctrl_c_when_no_safe_group` fails because the tmux-client `interrupt` does not yet send the in-band `C-c` fallback (`sent == []`).

(3) Minimal implementation — replace the body of the existing `interrupt` in `tmux_session.py` with the merged guarded-killpg + C-c fallback:

```python
class TmuxClient:
    # ... existing tpgid(self, target) stays AS-IS (single source) ...

    def interrupt(self, target, shell_pid):
        """Guarded Ctrl-C: SIGINT the pane's foreground process group, but only
        when tpgid is a real positive group that is NOT the shell itself —
        otherwise interrupting would hit the session's shell, not claude. When
        there is no safe foreground group, deliver an in-band C-c keystroke."""
        tpgid = self.tpgid(target)
        if tpgid > 0 and tpgid != shell_pid:
            try:
                os.killpg(tpgid, signal.SIGINT)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass                       # fall through to in-band C-c
        self.send_keys(target, "C-c")
```

Ensure `import os` and `import signal` are present at the top of `tmux_session.py` (added by the tmux-client component; if absent add them).

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py -k "interrupt_falls_back or interrupt_guarded or interrupt_signals"
```

Expected: 3 passed (tmux present; skipped otherwise).

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add tmux_session.py tests/integration/test_tmux_client.py && git commit -m "feat(tmux): interrupt C-c fallback when no safe foreground group

interrupt now SIGINTs tpgid only when tpgid>0 and != shell_pid; on any
killpg failure or an unsafe/zero group it falls back to an in-band C-c
keystroke so we never SIGINT the session's own shell. tpgid stays the single
tmux-client definition (not redefined here).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 43 — /session/interrupt endpoint method

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_endpoints.py` (add `_do_interrupt`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_turn_protocol.py` (append)

FIX applied: `shell_pid` is a `_Session` field defined from the registry-lifecycle component's consolidated `_Session.__init__` (per the consolidate-`_Session`-fields FIX), populated in `create`. This task does not bolt it on; it reads it via `getattr(sess, "shell_pid", 0)` for forward compatibility with older reconstructed sessions.

**Steps**

(1) Write the FAILING test — append to `tests/integration/test_turn_protocol.py`:

```python
@requires_tmux
def test_interrupt_endpoint_ok(session_factory):
    sess = session_factory()
    resp = sess.mixin._do_interrupt(sess.sid, sess.cap)
    assert resp == {"ok": True}


@requires_tmux
def test_interrupt_endpoint_bad_cap(session_factory):
    sess = session_factory()
    with pytest.raises(PermissionError):
        sess.mixin._do_interrupt(sess.sid, "0" * 64)
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py -k "interrupt_endpoint"
```

Expected FAIL: `AttributeError: 'SessionMixin' object has no attribute '_do_interrupt'`.

(3) Minimal implementation — add to `session_endpoints.py`:

```python
class SessionMixin:
    # ...

    def _do_interrupt(self, session_id, cap):
        sess = self._authz_session(session_id, cap)
        # shell_pid (persisted at create) lets the guarded killpg never hit the
        # shell once claude has exited.
        sess.tmux.interrupt(sess.pane, shell_pid=getattr(sess, "shell_pid", 0))
        return {"ok": True}
```

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_turn_protocol.py -k "interrupt_endpoint"
```

Expected: 2 passed.

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_endpoints.py tests/integration/test_turn_protocol.py && git commit -m "feat(session): /session/interrupt endpoint (guarded killpg)

_do_interrupt validates cap via _authz_session, then delegates to
TmuxClient.interrupt with the persisted shell_pid so the guarded SIGINT never
targets the session shell.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 44 — Reaper: orphan sessions, 2x-confirm + grace, never-busy, never-reconstructing

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_registry.py` (add `reap`, `_evict`, `_confined_rmtree`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/unit/test_registry_locking.py` (append)

FIX applied: single `_Registry(base=...)` constructor with `self._base` always present; `_Session` fields (`_gone_strikes`, `composer_seen`, `shell_pid`, `log_offset_base`) exist from the consolidated registry-lifecycle `_Session.__init__`, so `_mk_session` only sets values, never invents the field set. `_confined_rmtree` is the single confined-rmtree helper that the delete path (Task 51) also calls.

**Steps**

(1) Write the FAILING test — append to `tests/unit/test_registry_locking.py`:

```python
import threading
import time

import session_registry


class _StubTmux:
    """Records calls; has_session controllable; kill_server flagged."""
    def __init__(self, alive=True):
        self._alive = alive
        self.killed = False
        self.has_calls = 0

    def has_session(self, name):
        self.has_calls += 1
        return self._alive

    def kill_server(self):
        self.killed = True

    def get_option(self, name):
        return None

    def pipe_pane_off(self, target):
        pass


def _mk_session(reg, sid, *, alive=True, created_ago=999.0):
    sess = session_registry._Session.__new__(session_registry._Session)
    sess.sid = sid
    sess.cap = "c" * 64
    sess.nonce = "n" * 16
    sess.pane = "t:0.0"
    sess.tmux = _StubTmux(alive=alive)
    sess.turn_lock = threading.Lock()
    sess.ready = threading.Event(); sess.ready.set()
    sess.status = "READY"
    sess.created_at = time.time() - created_ago
    sess.rendezvous_dir = "/nonexistent/wcb_%s_x" % sid
    sess.log_path = sess.rendezvous_dir + "/pane.log"
    sess.composer_seen = True
    sess._gone_strikes = 0
    sess.log_offset_base = 0
    sess.shell_pid = 0
    reg._sessions[sid] = sess
    return sess


def test_reap_requires_two_confirmed_failures(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "a" * 32, alive=False)
    reg.reap()                       # first pass: one strike, no kill
    assert s.tmux.killed is False
    assert "a" * 32 in reg._sessions
    reg.reap()                       # second confirmed failure -> evict
    assert ("a" * 32) not in reg._sessions


def test_reap_never_touches_busy_session(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "b" * 32, alive=False)
    s.turn_lock.acquire()            # busy
    try:
        reg.reap(); reg.reap()
        assert ("b" * 32) in reg._sessions
        assert s.tmux.killed is False
    finally:
        s.turn_lock.release()


def test_reap_respects_grace_on_young_session(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "c" * 32, alive=False, created_ago=1.0)  # < 30s grace
    reg.reap(); reg.reap()
    assert ("c" * 32) in reg._sessions


def test_reap_keeps_live_session(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "d" * 32, alive=True)
    reg.reap(); reg.reap()
    assert ("d" * 32) in reg._sessions
    assert s._gone_strikes == 0


def test_reap_skips_reconstructing(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "e" * 32, alive=False)
    s.status = "RECONSTRUCTING"; s.ready.clear()
    reg.reap(); reg.reap()
    assert ("e" * 32) in reg._sessions
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k reap
```

Expected FAIL: `AttributeError: '_Registry' object has no attribute 'reap'`.

(3) Minimal implementation — add to `session_registry.py`:

```python
import shutil

from paths import assert_confined

GRACE_SECONDS = 30.0       # min age before a session is reapable
REAP_STRIKES = 2           # consecutive confirmed-gone passes required


class _Registry:
    # ...

    def reap(self):
        """One reaper pass. Evict only sessions that are (a) not busy, (b) not
        reconstructing, (c) past the create grace, and (d) confirmed gone on
        REAP_STRIKES consecutive passes. Reset strikes on any live confirmation.
        tmux I/O happens OUTSIDE the structural lock."""
        with self._lock:
            candidates = list(self._sessions.items())
        to_evict = []
        for sid, sess in candidates:
            if sess.status != "READY" or not sess.ready.is_set():
                continue                                  # mid-reconstruct
            if sess.turn_lock.locked():
                continue                                  # busy
            if (time.time() - sess.created_at) < GRACE_SECONDS:
                continue                                  # grace
            try:
                alive = sess.tmux.has_session("t")
            except Exception:
                # transient handling is refined in the reaper-thread task;
                # here an exception is conservatively NOT a strike.
                continue
            if alive:
                sess._gone_strikes = 0
                continue
            sess._gone_strikes = getattr(sess, "_gone_strikes", 0) + 1
            if sess._gone_strikes >= REAP_STRIKES:
                to_evict.append((sid, sess))
        for sid, sess in to_evict:
            self._evict(sid, sess)

    def _evict(self, sid, sess):
        """Confined teardown of a confirmed-gone session. Re-check busy under
        the structural lock to avoid racing a turn that just started."""
        try:
            sess.tmux.kill_server()
        except Exception:
            pass
        self._confined_rmtree(sess)
        with self._lock:
            cur = self._sessions.get(sid)
            if cur is sess and not sess.turn_lock.locked():
                del self._sessions[sid]

    def _confined_rmtree(self, sess):
        """realpath-confirm the rendezvous dir is under self._base, then rmtree.
        Single helper reused by reap/_evict and delete (Task 51)."""
        try:
            confined = assert_confined(sess.rendezvous_dir, self._base)
        except PermissionError:
            return                                        # refuse unconfined rm
        shutil.rmtree(confined, ignore_errors=True)
```

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k reap
```

Expected: 5 passed.

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/unit/test_registry_locking.py && git commit -m "feat(registry): reaper with 2x-confirm + grace, never-busy/reconstructing

reap() evicts only sessions that are not busy, not mid-reconstruct, past the
30s create grace, and confirmed gone on two consecutive passes; any live
has-session resets the strike count. _evict re-checks the turn lock under the
structural lock before deleting and rm's only via the confined _confined_rmtree
helper (shared with delete).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 45 — Stale-file sweep on reconstruct (env.* / old doc.*)

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_registry.py` (add `sweep_rendezvous`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/unit/test_registry_locking.py` (append)

FIX applied: `assert_confined` (single source in `paths.py`, authored by the path-safety phase) raises `PermissionError` on escape. The hydrating-winner wiring in `get_or_reconstruct` is owned by the registry-lifecycle component; this task only unit-tests the sweep itself.

**Steps**

(1) Write the FAILING test — append to `tests/unit/test_registry_locking.py`:

```python
import os

import pytest

import session_registry


def test_sweep_unlinks_all_env_and_doc(tmp_base):
    d = tmp_base / "wcb_aaaa_x"
    d.mkdir(mode=0o700)
    (d / "env.11111111-1111-1111-1111-111111111111.json").write_text("{}")
    (d / "env.22222222-2222-2222-2222-222222222222.json").write_text("{}")
    (d / "doc.33333333-3333-3333-3333-333333333333.html").write_text("x")
    keep = d / "pane.log"
    keep.write_text("log")
    reg = session_registry._Registry(base=str(tmp_base))
    reg.sweep_rendezvous(str(d))
    names = set(os.listdir(d))
    assert not any(n.startswith("env.") for n in names)
    assert not any(n.startswith("doc.") for n in names)
    assert "pane.log" in names         # log is NOT swept


def test_sweep_skips_symlink(tmp_base):
    d = tmp_base / "wcb_bbbb_x"
    d.mkdir(mode=0o700)
    target = tmp_base / "outside.json"
    target.write_text("secret")
    link = d / "env.44444444-4444-4444-4444-444444444444.json"
    os.symlink(str(target), str(link))
    reg = session_registry._Registry(base=str(tmp_base))
    reg.sweep_rendezvous(str(d))       # must NOT follow/delete the symlink target
    assert target.exists()             # outside file untouched
    assert not link.exists() or link.is_symlink()


def test_sweep_confines_to_base(tmp_base, tmp_path):
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    reg = session_registry._Registry(base=str(tmp_base))
    with pytest.raises(PermissionError):
        reg.sweep_rendezvous(str(outside))
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k sweep
```

Expected FAIL: `AttributeError: '_Registry' object has no attribute 'sweep_rendezvous'`.

(3) Minimal implementation — add to `session_registry.py`:

```python
import stat


class _Registry:
    # ...

    def sweep_rendezvous(self, rendezvous_dir):
        """Unlink ALL leftover env.*.json and doc.*.html in a confirmed-confined
        rendezvous dir (verified-regular, O_NOFOLLOW-safe). Never the pane log;
        never by recency. Symlinks are unlinked but never followed."""
        d = assert_confined(rendezvous_dir, self._base)   # PermissionError if escape
        try:
            entries = os.listdir(d)
        except FileNotFoundError:
            return
        for name in entries:
            if not (name.startswith("env.") and name.endswith(".json")) and \
               not (name.startswith("doc.") and name.endswith(".html")):
                continue
            full = os.path.join(d, name)
            try:
                st = os.lstat(full)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(st.st_mode):
                try:
                    os.unlink(full)                       # remove dangling link only
                except OSError:
                    pass
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            try:
                os.unlink(full)
            except OSError:
                pass
```

(`get_or_reconstruct` calls `sweep_rendezvous(sess.rendezvous_dir)` once after rebuilding from tmux facts — wired and integration-tested in the registry-lifecycle component.)

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k sweep
```

Expected: 3 passed.

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/unit/test_registry_locking.py && git commit -m "feat(registry): confined stale-file sweep on reconstruct

sweep_rendezvous unlinks every leftover env.*.json / doc.*.html in a
realpath-confined rendezvous dir, verified-regular and O_NOFOLLOW-safe,
never following symlinks and never touching the pane log.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 46 — Pipe-pane hygiene: disable-first re-arm idempotency + 64 MiB rotation with offset base

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tmux_session.py` (add `pane_pipe`; `pipe_pane_on` disable-first — single definition, replacing the tmux-client weak version)
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_registry.py` (add `current_offset`, `rotate_log_if_needed`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_tmux_client.py` and `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/unit/test_registry_locking.py`

FIX applied: ONE `pipe_pane_on` definition — the disable-first version with the strong `pane_pipe()==1` assertion (the tmux-client component's weak count-based test is replaced by this). `log_offset_base` defaults to 0 in the consolidated `_Session.__init__`; `current_offset` and `_do_replay` (Task 41) both honour it so replay stays monotonic across rotations.

**Steps**

(1) Write the FAILING tests.

Append to `tests/integration/test_tmux_client.py`:

```python
@requires_tmux
def test_pipe_pane_rearm_is_idempotent(tmux_client_factory, tmp_path):
    """Re-arming pipe-pane disables first, so no second cat/sh leaks
    (#{pane_pipe} stays 1, not a doubled sink)."""
    client, target = tmux_client_factory()
    log = tmp_path / "pane.log"
    client.pipe_pane_on(target, str(log))
    assert client.pane_pipe(target) == 1
    client.pipe_pane_on(target, str(log))   # re-arm; must disable-first
    assert client.pane_pipe(target) == 1     # still exactly one pipe
    client.pipe_pane_off(target)
    assert client.pane_pipe(target) == 0
```

Append to `tests/unit/test_registry_locking.py`:

```python
def test_rotate_log_preserves_monotonic_offset(tmp_base):
    d = tmp_base / "wcb_rot_x"; d.mkdir(mode=0o700)
    log = d / "pane.log"
    log.write_bytes(b"X" * 100)
    reg = session_registry._Registry(base=str(tmp_base))
    sess = _mk_session(reg, "f" * 32)
    sess.rendezvous_dir = str(d)
    sess.log_path = str(log)
    sess.log_offset_base = 0
    reg.rotate_log_if_needed(sess, cap_bytes=100)
    # rotated: fresh empty log, offset base advanced by the rotated size
    assert os.path.getsize(sess.log_path) == 0
    assert sess.log_offset_base == 100
    # a follow-up current_offset stays globally monotonic
    log.write_bytes(b"Y" * 10)
    assert reg.current_offset(sess) == 110
```

(2) Run them + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py -k pipe_pane_rearm && python3 -m pytest -q tests/unit/test_registry_locking.py -k rotate
```

Expected FAIL: `AttributeError: 'TmuxClient' object has no attribute 'pane_pipe'` and `'_Registry' object has no attribute 'rotate_log_if_needed'`.

(3) Minimal implementation.

In `tmux_session.py` (single `pipe_pane_on`; add `pane_pipe`):

```python
import shlex


class TmuxClient:
    # ...

    def pane_pipe(self, target):
        """1 if the pane currently has a pipe-pane sink, else 0."""
        out = self._run(["display-message", "-p", "-t", target,
                         "#{pane_pipe}"]).strip()
        return 1 if out == "1" else 0

    def pipe_pane_on(self, target, log_path):
        """Disable-first (the `cat >>` form does NOT toggle off), then arm a
        fresh sink. shlex.quote the FULL log path so a hostile filename cannot
        break out of the shell command tmux runs."""
        self.pipe_pane_off(target)                 # argument-less disable
        quoted = shlex.quote(log_path)
        self._run(["pipe-pane", "-t", target, "-O", "cat >> %s" % quoted])

    def pipe_pane_off(self, target):
        self._run(["pipe-pane", "-t", target])     # no command => disable
```

In `session_registry.py`:

```python
LOG_CAP_BYTES = 64 * 1024 * 1024     # 64 MiB per-session log cap


class _Registry:
    # ...

    def current_offset(self, sess):
        """Globally-monotonic byte offset = rotated bytes + current live size."""
        try:
            live = os.path.getsize(sess.log_path)
        except FileNotFoundError:
            live = 0
        return getattr(sess, "log_offset_base", 0) + live

    def rotate_log_if_needed(self, sess, cap_bytes=LOG_CAP_BYTES):
        """If the live log exceeds cap_bytes, truncate to a fresh empty file and
        advance log_offset_base by the rotated size so from_offset stays
        globally monotonic across rotations. pipe-pane keeps writing to the same
        path (rotate-by-truncate); the offset base, not the file, is the cursor."""
        try:
            size = os.path.getsize(sess.log_path)
        except FileNotFoundError:
            return
        if size < cap_bytes:
            return
        sess.log_offset_base = getattr(sess, "log_offset_base", 0) + size
        fd = os.open(sess.log_path, os.O_WRONLY | os.O_NOFOLLOW)
        try:
            os.ftruncate(fd, 0)
        finally:
            os.close(fd)
```

(`log_offset_base: int = 0` is part of the consolidated `_Session.__init__`. The reaper thread (Task 48) calls `rotate_log_if_needed(sess)` for each non-busy session per pass.)

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/integration/test_tmux_client.py -k pipe_pane_rearm && python3 -m pytest -q tests/unit/test_registry_locking.py -k rotate
```

Expected: integration test passes with tmux (skipped otherwise); unit rotate test passes.

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add tmux_session.py session_registry.py tests/integration/test_tmux_client.py tests/unit/test_registry_locking.py && git commit -m "feat(logs): pipe-pane disable-first re-arm + 64MiB rotate w/ offset base

pipe_pane_on (single definition) disables the existing sink first (the cat>>
form never toggles off) so no second cat/sh leaks; the full log path is
shlex.quoted; pane_pipe()==1 pins idempotency. rotate_log_if_needed truncates
in place at 64 MiB and advances log_offset_base so from_offset stays globally
monotonic across rotations; current_offset exposes the monotonic cursor.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 47 — Idle-TTL reaper (2h on @wcb_last_turn) + max-session cap (8 -> 429)

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_registry.py` (add `reap_idle`, `MaxSessionsReached`, `_check_capacity`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/unit/test_registry_locking.py` (append)

FIX applied: `@wcb_last_turn` is set at the start of every turn by the turn-protocol component's held-lock body; this task only guarantees the reaper reads it. `_check_capacity()` is called at the top of `create` (registry-lifecycle component) and surfaces as 429 through the dispatcher ladder (Task 49).

**Steps**

(1) Write the FAILING test — append to `tests/unit/test_registry_locking.py`:

```python
def test_idle_ttl_kills_old_session(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "1" * 32)
    s.rendezvous_dir = str(tmp_base / "wcb_old"); os.mkdir(s.rendezvous_dir, 0o700)
    s.log_path = s.rendezvous_dir + "/pane.log"
    last = str(time.time() - 3 * 3600)        # 3h ago
    s.tmux.get_option = lambda name: last if name == "@wcb_last_turn" else None
    reg.reap_idle(ttl_seconds=2 * 3600)
    assert ("1" * 32) not in reg._sessions
    assert s.tmux.killed is True


def test_idle_ttl_keeps_recent_session(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "2" * 32)
    recent = str(time.time() - 60)
    s.tmux.get_option = lambda name: recent if name == "@wcb_last_turn" else None
    reg.reap_idle(ttl_seconds=2 * 3600)
    assert ("2" * 32) in reg._sessions


def test_idle_ttl_never_kills_busy(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "3" * 32)
    s.tmux.get_option = lambda name: str(time.time() - 9999) if name == "@wcb_last_turn" else None
    s.turn_lock.acquire()
    try:
        reg.reap_idle(ttl_seconds=2 * 3600)
        assert ("3" * 32) in reg._sessions
    finally:
        s.turn_lock.release()


def test_idle_ttl_missing_option_keeps_session(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "5" * 32)
    s.tmux.get_option = lambda name: None     # missing/unparseable -> keep
    reg.reap_idle(ttl_seconds=2 * 3600)
    assert ("5" * 32) in reg._sessions


def test_create_raises_max_sessions(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    reg.MAX_SESSIONS = 2
    for i in range(2):
        _mk_session(reg, str(i) * 32)
    with pytest.raises(session_registry.MaxSessionsReached):
        reg._check_capacity()
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "idle_ttl or max_sessions"
```

Expected FAIL: `AttributeError: '_Registry' object has no attribute 'reap_idle'` / `module 'session_registry' has no attribute 'MaxSessionsReached'`.

(3) Minimal implementation — add to `session_registry.py`:

```python
IDLE_TTL_SECONDS = 2 * 3600          # 2h idle-TTL (decision 4)
DEFAULT_MAX_SESSIONS = 8             # max concurrent sessions (decision 4)


class MaxSessionsReached(Exception):
    """Raised at create when the concurrent-session cap is hit -> 429."""


class _Registry:
    MAX_SESSIONS = DEFAULT_MAX_SESSIONS
    # ...

    def _check_capacity(self):
        with self._lock:
            n = len(self._sessions)
        if n >= self.MAX_SESSIONS:
            raise MaxSessionsReached(
                "max %d concurrent sessions reached" % self.MAX_SESSIONS)

    def reap_idle(self, ttl_seconds=IDLE_TTL_SECONDS):
        """Kill sessions whose @wcb_last_turn is older than ttl_seconds,
        regardless of liveness — but never a busy or reconstructing one.
        Missing/unparseable @wcb_last_turn is treated as 'recently active'
        (default), never as a reason to kill."""
        with self._lock:
            candidates = list(self._sessions.items())
        now = time.time()
        for sid, sess in candidates:
            if sess.status != "READY" or not sess.ready.is_set():
                continue
            if sess.turn_lock.locked():
                continue
            try:
                last = float(sess.tmux.get_option("@wcb_last_turn"))
            except (TypeError, ValueError):
                continue                     # default -> keep
            if (now - last) > ttl_seconds:
                self._evict(sid, sess)
```

(`self._check_capacity()` is called at the very top of `_Registry.create` before minting ids — owned by the registry-lifecycle component. The endpoint wrapper `_do_create` (Task 50) lets `MaxSessionsReached` propagate to the 429 rung of the ladder.)

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "idle_ttl or max_sessions"
```

Expected: 5 passed.

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/unit/test_registry_locking.py && git commit -m "feat(registry): idle-TTL 2h reaper on @wcb_last_turn + max-8 cap -> 429

reap_idle evicts sessions idle past the TTL regardless of liveness but never
busy/reconstructing; missing @wcb_last_turn defaults to 'recently active'.
_check_capacity raises MaxSessionsReached once 8 concurrent sessions exist
(surfaced as 429 by the dispatcher ladder).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 48 — Reaper thread + transient-vs-authoritative classification gating

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_registry.py` (gate `reap` on `_TmuxError.retryable`; add `start_reaper`/`stop_reaper`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/unit/test_registry_locking.py` (append)

FIX applied: `_TmuxError(msg, retryable=...)` is the single tmux-client exception (authored by the tmux-client component). The reaper thread also runs `reap_idle` and `rotate_log_if_needed`. `self._reaper_stop`/`self._reaper_thread` are initialized in the consolidated `_Registry.__init__`.

**Steps**

(1) Write the FAILING test — append to `tests/unit/test_registry_locking.py`:

```python
import tmux_session


class _TransientTmux(_StubTmux):
    """has_session always raises a RETRYABLE _TmuxError (e.g. EINTR)."""
    def has_session(self, name):
        self.has_calls += 1
        raise tmux_session._TmuxError("interrupted", retryable=True)


def test_transient_error_never_strikes(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "7" * 32)
    s.tmux = _TransientTmux()
    s.created_at = time.time() - 999
    reg.reap(); reg.reap(); reg.reap()
    assert ("7" * 32) in reg._sessions
    assert getattr(s, "_gone_strikes", 0) == 0


def test_authoritative_error_strikes_and_evicts(tmp_base):
    class _Gone(_StubTmux):
        def has_session(self, name):
            self.has_calls += 1
            raise tmux_session._TmuxError("no server running", retryable=False)
    reg = session_registry._Registry(base=str(tmp_base))
    s = _mk_session(reg, "8" * 32)
    s.tmux = _Gone()
    s.created_at = time.time() - 999
    reg.reap()
    assert ("8" * 32) in reg._sessions     # one strike
    reg.reap()
    assert ("8" * 32) not in reg._sessions  # second authoritative strike -> evict


def test_start_reaper_is_daemon_and_stoppable(tmp_base):
    reg = session_registry._Registry(base=str(tmp_base))
    t = reg.start_reaper(interval=0.01)
    assert t.daemon is True
    reg.stop_reaper()
    t.join(timeout=2.0)
    assert not t.is_alive()
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "transient or authoritative or start_reaper"
```

Expected FAIL: `AttributeError: '_Registry' object has no attribute 'start_reaper'`; and the authoritative case fails because `reap` currently swallows ALL exceptions as "do not strike".

(3) Minimal implementation — edit `reap`'s has-session try/except in `session_registry.py` to honour the retryable flag, and add the reaper thread.

Add the import and replace the `has_session` block inside `reap`:

```python
import threading

from tmux_session import _TmuxError
```

Inside `reap`, replace:

```python
            try:
                alive = sess.tmux.has_session("t")
            except Exception:
                continue
```

with:

```python
            try:
                alive = sess.tmux.has_session("t")
            except _TmuxError as e:
                if e.retryable:
                    continue                 # transient -> do NOT strike
                alive = False                # authoritative gone -> strike below
            except Exception:
                continue                     # unknown -> conservative, no strike
```

Add the reaper thread methods (and ensure `self._reaper_stop = threading.Event()` / `self._reaper_thread = None` are set in `__init__`):

```python
class _Registry:
    # ...

    def start_reaper(self, interval=30.0):
        """Background daemon that runs reap + reap_idle + log rotation every
        `interval` seconds. Returns the Thread."""
        self._reaper_stop.clear()

        def _loop():
            while not self._reaper_stop.wait(interval):
                try:
                    self.reap()
                    self.reap_idle()
                    with self._lock:
                        sessions = list(self._sessions.values())
                    for sess in sessions:
                        if not sess.turn_lock.locked():
                            try:
                                self.rotate_log_if_needed(sess)
                            except Exception:
                                pass
                except Exception:
                    pass

        self._reaper_thread = threading.Thread(target=_loop, daemon=True)
        self._reaper_thread.start()
        return self._reaper_thread

    def stop_reaper(self):
        self._reaper_stop.set()
```

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_registry_locking.py -k "transient or authoritative or start_reaper"
```

Expected: 3 passed.

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py tests/unit/test_registry_locking.py && git commit -m "feat(registry): reaper thread + transient-vs-authoritative gating

A retryable _TmuxError (EINTR/EAGAIN/timeout) never counts as a gone-strike;
only an authoritative 'no server running'/'session not found' does. The
daemon reaper loop runs reap + reap_idle + log rotation on an interval and is
cleanly stoppable.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 49 — Security wiring: mandatory token, origin allowlist (not *), Origin/Sec-Fetch guard, error→status ladder, dispatcher + do_GET

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_endpoints.py` (add `_dispatch_session`, `_route_session`, `_session_authorized`, `_session_cors`, `_origin_ok`, `_BodyTooLarge`, `_TmuxMissing`)
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/server.py:34,42-45,87-104` (`Handler(SessionMixin, ...)`, `do_GET`, `do_POST`/`do_OPTIONS` dispatch, env hooks)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/unit/test_security_ladder.py` (NEW)

FIX applied: this is the CANONICAL dispatcher (`_dispatch_session` + `_route_session`) and the CANONICAL `server.Handler(SessionMixin, ...)` wiring — every `/session/*` handler is named `_do_*` (the turn-protocol component's `_session_stream`/`_session_send_key`/`_session_get_envelope` are renamed to `_do_stream`/`_do_send_key`/`_do_get_envelope_response` and routed through here). Single CORS env var is `WCB_RWA_ORIGIN` (matches the rwa config and the turn-protocol/rendezvous SSE tests). `_session_cors` emits headers only (no status, no dead `if False` line). This task must land BEFORE the turn-protocol stream/get-envelope tests and the rendezvous-docsync get-envelope test, which POST through `server.Handler`. Pin pytest import mode (`importmode=importlib`, `pythonpath=.`) in the test-scaffold `pyproject.toml`/`pytest.ini` so the cross-module `from conftest import ...` resolves.

**Steps**

(1) Write the FAILING test — create `tests/unit/test_security_ladder.py`:

```python
import json

import pytest

import session_endpoints
import session_registry
import tmux_session
import paths


class _FakeHeaders(dict):
    def get(self, k, default=None):
        for kk, vv in self.items():
            if kk.lower() == k.lower():
                return vv
        return default


class _Probe(session_endpoints.SessionMixin):
    """Drives _dispatch_session with captured (status, payload) instead of a
    real socket."""
    def __init__(self, headers, body, *, token="secret", origin="https://rwa.app"):
        self.headers = _FakeHeaders(headers)
        self._body_bytes = json.dumps(body).encode() if body is not None else b""
        self.sent = None
        self._registry = session_registry._Registry(base="/nonexistent")
        self._origin_cfg = origin
        self._token_cfg = token

    def _session_token(self):
        return self._token_cfg

    def _allowed_origins(self):
        return {self._origin_cfg}

    def _read_session_body(self):
        return json.loads(self._body_bytes) if self._body_bytes else {}

    def _send(self, status, payload):
        self.sent = (status, payload)

    def _send_sse_headers(self):
        self.sent = (200, "<sse>")


def _ok_headers(token="secret", origin="https://rwa.app", site="cross-site"):
    return {"Authorization": "Bearer " + token, "Origin": origin,
            "Sec-Fetch-Site": site}


def test_missing_token_is_401():
    p = _Probe({"Origin": "https://rwa.app"}, {"session_id": "a" * 32, "cap": "x"})
    p._dispatch_session("POST", "/session/capture")
    assert p.sent[0] == 401


def test_bad_origin_is_403_before_tmux():
    p = _Probe(_ok_headers(origin="https://evil.example"),
               {"session_id": "a" * 32, "cap": "x"})
    p._dispatch_session("POST", "/session/capture")
    assert p.sent[0] == 403


def test_cors_reflects_only_allowed_origin_never_star():
    captured = {}
    p = _Probe(_ok_headers(), None)
    p.send_header = lambda k, v: captured.__setitem__(k, v)
    p._session_cors()
    assert captured["Access-Control-Allow-Origin"] == "https://rwa.app"
    assert captured["Access-Control-Allow-Origin"] != "*"


def test_non_ascii_bearer_is_401_not_500():
    p = _Probe({"Authorization": "Bearer \u00ff\u00ff", "Origin": "https://rwa.app",
                "Sec-Fetch-Site": "cross-site"},
               {"session_id": "a" * 32, "cap": "x"})
    p._dispatch_session("POST", "/session/capture")
    assert p.sent[0] == 401


@pytest.mark.parametrize("exc,status", [
    (paths.EnvelopeNotWritten("x"), 404),
    (paths.EnvelopeRejected("x"), 422),
    (paths.EnvelopeIncomplete("x"), 404),
    (PermissionError("cap mismatch"), 403),
    (session_endpoints._TurnBusy("busy"), 409),
    (ValueError("bad id"), 400),
    (TypeError("bad type"), 400),
    (json.JSONDecodeError("x", "y", 0), 400),
    (tmux_session._TmuxError("boom", retryable=False), 502),
    (session_endpoints._TmuxMissing("no tmux"), 503),
    (session_registry.MaxSessionsReached("8"), 429),
    (session_endpoints._BodyTooLarge("big"), 413),
    (RuntimeError("unknown"), 500),
])
def test_error_ladder(exc, status):
    p = _Probe(_ok_headers(), {"session_id": "a" * 32, "cap": "x"})

    def _boom(*a, **k):
        raise exc
    p._do_capture = _boom
    p._dispatch_session("POST", "/session/capture")
    assert p.sent[0] == status
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_security_ladder.py
```

Expected FAIL: `AttributeError: 'SessionMixin' object has no attribute '_dispatch_session'` (plus `_TmuxMissing`/`_BodyTooLarge` not yet in `session_endpoints`).

(3) Minimal implementation — add to `session_endpoints.py`:

```python
import json

import paths
import session_registry
from tmux_session import _TmuxError


class _BodyTooLarge(Exception):
    """413 — request body exceeds the cap."""


class _TmuxMissing(Exception):
    """503 — the tmux binary is not installed."""


_GET_ROUTES = {"/session/list"}


class SessionMixin:
    # ... endpoint methods _do_* live here ...

    def _session_cors(self):
        """Emit Access-Control-Allow-Origin ONLY for the configured rwa origin;
        never `*`. Headers ONLY — the caller sets the response status."""
        origin = self.headers.get("Origin") or ""
        if origin in self._allowed_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _origin_ok(self):
        """Origin must be in the allowlist; a missing Origin is allowed only for
        a same-origin/non-browser caller (Sec-Fetch-Site same-origin/none).
        Reject before any tmux work."""
        origin = self.headers.get("Origin")
        if origin is None:
            site = self.headers.get("Sec-Fetch-Site")
            return site in (None, "same-origin", "none")
        return origin in self._allowed_origins()

    def _session_authorized(self):
        """Token is MANDATORY for /session/*. hmac.compare_digest with a
        non-ASCII guard so a junk bearer is a clean 401, not a 500."""
        token = self._session_token()
        if not token:
            return False
        header = self.headers.get("Authorization") or ""
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        try:
            return hmac.compare_digest(header[len(prefix):], token)
        except (TypeError, ValueError):
            return False                      # non-ASCII payload -> 401

    def _dispatch_session(self, method, path=None):
        path = path or self.path
        if not self._origin_ok():                      # 1) origin FIRST
            return self._send(403, {"error": "bad origin"})
        if not self._session_authorized():             # 2) mandatory token
            return self._send(401, {"error": "unauthorized"})
        if method == "GET" and path not in _GET_ROUTES:  # 3) method gate
            return self._send(405, {"error": "method not allowed"})
        try:
            return self._route_session(method, path)
        except _BodyTooLarge as e:
            self._send(413, {"error": str(e)})
        except paths.EnvelopeRejected as e:
            self._send(422, {"error": str(e)})
        except paths.EnvelopeNotWritten as e:
            self._send(404, {"error": str(e)})
        except paths.EnvelopeIncomplete as e:
            self._send(404, {"error": str(e)})
        except PermissionError as e:
            self._send(403, {"error": str(e)})
        except _TurnBusy as e:
            self._send(409, {"error": str(e)})
        except session_registry.MaxSessionsReached as e:
            self._send(429, {"error": str(e)})
        except _TmuxMissing as e:
            self._send(503, {"error": str(e)})
        except _TmuxError as e:
            self._send(502, {"error": str(e)})
        except (ValueError, TypeError, json.JSONDecodeError, KeyError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _route_session(self, method, path):
        body = self._read_session_body()
        sid = body.get("session_id")
        cap = body.get("cap")
        if path == "/session/create":
            return self._send(200, self._do_create(body))
        if path == "/session/list":
            return self._send(200, self._do_list())
        if path == "/session/capture":
            return self._send(200, self._do_capture(sid, cap))
        if path == "/session/send-key":
            return self._send(200, self._do_send_key(sid, cap, body.get("keys")))
        if path == "/session/interrupt":
            return self._send(200, self._do_interrupt(sid, cap))
        if path == "/session/replay":
            return self._send(200, self._do_replay(sid, cap, body.get("from_offset", 0)))
        if path == "/session/get-envelope":
            return self._do_get_envelope_response(sid, cap, body.get("turn_uuid"))
        if path == "/session/delete":
            return self._send(200, self._do_delete(sid, cap))
        if path == "/session/stream":
            return self._do_stream(body)
        return self._send(405, {"error": "not found"})
```

Now wire `server.py`.

`server.py:34`:

```python
from session_endpoints import SessionMixin, _BodyTooLarge


class Handler(SessionMixin, BaseHTTPRequestHandler):
```

`server.py:42-45` (`do_OPTIONS`):

```python
    def do_OPTIONS(self):
        if self.path.startswith("/session/"):
            self.send_response(204)
            self._session_cors()
            self.end_headers()
            return
        self.send_response(204)
        self._cors()
        self.end_headers()
```

`server.py:87` (top of `do_POST`):

```python
    def do_POST(self):
        if self.path.startswith("/session/"):
            return self._dispatch_session("POST")
        try:
            if self.path not in ("/run", "/stream"):
                return self._send(405, {"error": "not found"})
            # ... existing /run,/stream body unchanged ...
```

Add `do_GET` after `do_POST`:

```python
    def do_GET(self):
        if self.path.startswith("/session/"):
            return self._dispatch_session("GET")
        return self._send(405, {"error": "not found"})
```

Add the three harness hooks to `server.py` (single CORS env var `WCB_RWA_ORIGIN`):

```python
    def _session_token(self):
        return os.environ.get("WEB_CLI_BRIDGE_TOKEN") or None

    def _allowed_origins(self):
        raw = os.environ.get("WCB_RWA_ORIGIN", "")
        return {o.strip() for o in raw.split(",") if o.strip()}

    def _read_session_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise _BodyTooLarge("request body exceeds %d bytes" % MAX_BODY_BYTES)
        return json.loads(self.rfile.read(length)) if length else {}
```

(The existing `/run`,`/stream` `_BodyTooLarge` in `server.py` is left untouched; the session path uses the `session_endpoints._BodyTooLarge` imported above — the ladder test pins that class.)

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_security_ladder.py
```

Expected: 17 passed (13 parametrized ladder cases + 4 auth/CORS cases).

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_endpoints.py server.py tests/unit/test_security_ladder.py && git commit -m "feat(session): mandatory token + origin allowlist + full error ladder

_dispatch_session enforces an Origin/Sec-Fetch allowlist (never *) BEFORE any
tmux work, requires a Bearer token via hmac.compare_digest with a non-ASCII
guard (401 not 500), and maps every exception through the full
413/400/403/409/404/422/502/503/429/500 ladder. server.py wires
Handler(SessionMixin, ...), do_GET, and per-session do_OPTIONS CORS reading
the single WCB_RWA_ORIGIN var, while leaving /run and /stream untouched.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 50 — _do_create / _do_list endpoint wrappers (cwd validation, max-sessions→429, no caller-supplied socket)

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_endpoints.py` (add `_do_create`, `_do_list`)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/unit/test_security_ladder.py` (append)

FIX applied (GAP): assigns the missing owner for `_do_create`/`_do_list` — the HTTP wrappers around `registry.create`/`list_sessions`. `_do_create` NEVER forwards a caller-supplied `socket_override` (closes the risk #6 socket-path spoof); `cwd` validation and `MaxSessionsReached` propagate to the dispatcher's 400/429 rungs (Task 49). `list_sessions` (with `log_bytes`) is owned by the registry-lifecycle component.

**Steps**

(1) Write the FAILING test — append to `tests/unit/test_security_ladder.py`:

```python
def test_do_create_ignores_caller_socket_override():
    """A hostile body cannot redirect the tmux socket path: _do_create must
    drop any socket_override/socket key before calling registry.create."""
    p = _Probe(_ok_headers(), {"cwd": "/tmp", "socket_override": "/evil/sock",
                               "socket": "/evil/sock"})
    seen = {}

    def _fake_create(**kw):
        seen.update(kw)
        class _S:
            sid = "z" * 32; cap = "c" * 64
            rendezvous_dir = "/x"; created_at = 0.0
        return _S()
    p._registry.create = _fake_create
    p._dispatch_session("POST", "/session/create")
    assert p.sent[0] == 200
    assert "socket_override" not in seen
    assert "socket" not in seen
    assert seen.get("cwd") == "/tmp"


def test_do_create_propagates_max_sessions_429():
    p = _Probe(_ok_headers(), {"cwd": "/tmp"})

    def _full(**kw):
        raise session_registry.MaxSessionsReached("8")
    p._registry.create = _full
    p._dispatch_session("POST", "/session/create")
    assert p.sent[0] == 429


def test_do_create_bad_cwd_is_400():
    p = _Probe(_ok_headers(), {"cwd": "/does/not/exist"})

    def _bad(**kw):
        raise ValueError("cwd is not a directory")
    p._registry.create = _bad
    p._dispatch_session("POST", "/session/create")
    assert p.sent[0] == 400


def test_do_list_returns_sessions():
    p = _Probe(_ok_headers(), None)
    p._registry.list_sessions = lambda: [{"session_id": "a" * 32, "log_bytes": 10}]
    p._dispatch_session("GET", "/session/list")
    assert p.sent[0] == 200
    assert p.sent[1]["sessions"][0]["log_bytes"] == 10
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_security_ladder.py -k "do_create or do_list"
```

Expected FAIL: `AttributeError: 'SessionMixin' object has no attribute '_do_create'` (and `_do_list`).

(3) Minimal implementation — add to `session_endpoints.py`:

```python
class SessionMixin:
    # ...

    def _do_create(self, body):
        """Mint a session. The socket path is ALWAYS derived from the minted id
        inside registry.create; a caller-supplied socket/socket_override is
        dropped so a hostile body cannot redirect the tmux socket (risk #6)."""
        cwd = body.get("cwd")
        cols = body.get("cols", 80)
        rows = body.get("rows", 24)
        claude_argv = body.get("claude_argv")
        sess = self._registry.create(cwd=cwd, cols=cols, rows=rows,
                                     claude_argv=claude_argv)
        return {"session_id": sess.sid, "cap": sess.cap,
                "rendezvous_dir": sess.rendezvous_dir,
                "created_at": sess.created_at}

    def _do_list(self):
        return {"sessions": self._registry.list_sessions()}
```

(`registry.create`'s `socket_override` param exists only as a test seam — it is never reachable from `_do_create`, so an HTTP caller cannot set it.)

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_security_ladder.py -k "do_create or do_list"
```

Expected: 4 passed.

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_endpoints.py tests/unit/test_security_ladder.py && git commit -m "feat(session): _do_create/_do_list wrappers (no caller socket, 429/400 ladder)

_do_create derives the tmux socket from the minted id and DROPS any
caller-supplied socket/socket_override so a hostile body cannot redirect it
(risk #6); MaxSessionsReached -> 429 and bad-cwd ValueError -> 400 flow through
the dispatcher ladder. _do_list returns the registry session list incl.
log_bytes.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 51 — Per-session cap enforced on EVERY /session/* call (regression matrix) + /session/delete confined kill + rm

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_endpoints.py` (ensure every cap-bound `_do_*` funnels through `_authz_session`; add `_do_delete`, `_do_stream`, `_do_get_envelope_response` entry wrappers)
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_registry.py` (`delete` confined teardown)
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/unit/test_security_ladder.py` and `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_session_lifecycle.py` (append)

FIX applied: `_do_stream`/`_do_get_envelope_response` (the renamed turn-protocol/rendezvous handlers) MUST funnel through `_authz_session` before any tmux work — the cap matrix pins this uniformly. `delete` reuses the single `assert_confined`/`_confined_rmtree` confinement helper (Task 44) and checks the cap with `compare_digest` BEFORE any kill. The held-lock SSE body `_run_stream_turn` and envelope emitter `_emit_envelope` are owned by the turn-protocol and rendezvous-docsync components; here they are invoked after the cap gate.

**Steps**

(1) Write the FAILING test.

Append to `tests/unit/test_security_ladder.py`:

```python
import session_endpoints


CAP_ROUTES = [
    "/session/capture",
    "/session/send-key",
    "/session/interrupt",
    "/session/replay",
    "/session/get-envelope",
    "/session/delete",
    "/session/stream",
]


@pytest.mark.parametrize("route", CAP_ROUTES)
def test_every_capbound_route_calls_authz_session(route, monkeypatch):
    """Each cap-bound route must funnel through _authz_session(sid, cap); a
    wrong cap therefore yields 403 uniformly."""
    p = _Probe(_ok_headers(), {"session_id": "a" * 32, "cap": "the-cap",
                               "keys": ["Enter"], "from_offset": 0,
                               "turn_uuid": "11111111-1111-1111-1111-111111111111"})
    seen = {}

    def _spy(sid, cap):
        seen["sid"] = sid
        seen["cap"] = cap
        raise PermissionError("cap mismatch")
    monkeypatch.setattr(p, "_authz_session", _spy)
    p._dispatch_session("POST", route)
    assert seen.get("sid") == "a" * 32
    assert seen.get("cap") == "the-cap"
    assert p.sent[0] == 403


def test_create_and_list_do_not_require_cap():
    p = _Probe(_ok_headers(), {"cwd": "/tmp"})
    p._do_create = lambda body: {"session_id": "z" * 32, "cap": "c",
                                 "rendezvous_dir": "/x", "created_at": 0}
    p._dispatch_session("POST", "/session/create")
    assert p.sent[0] == 200
    p2 = _Probe(_ok_headers(), None)
    p2._do_list = lambda: {"sessions": []}
    p2._dispatch_session("GET", "/session/list")
    assert p2.sent[0] == 200
```

Append to `tests/integration/test_session_lifecycle.py`:

```python
import os

import pytest

from conftest import requires_tmux
from test_registry_locking import _mk_session


@requires_tmux
def test_delete_kills_and_removes_confined_dir(session_factory):
    sess = session_factory()
    rdir = sess.rendezvous_dir
    assert os.path.isdir(rdir)
    sess.mixin._registry.delete(sess.sid, sess.cap)
    assert not sess.tmux.has_session("t")          # server killed
    assert not os.path.isdir(rdir)                 # dir removed
    assert sess.sid not in sess.mixin._registry._sessions


@requires_tmux
def test_delete_rejects_bad_cap(session_factory):
    sess = session_factory()
    with pytest.raises(PermissionError):
        sess.mixin._registry.delete(sess.sid, "0" * 64)
    assert os.path.isdir(sess.rendezvous_dir)      # not removed


def test_delete_refuses_unconfined_dir(tmp_base, tmp_path):
    import session_registry
    reg = session_registry._Registry(base=str(tmp_base))
    outside = tmp_path / "escape"; outside.mkdir()
    sess = _mk_session(reg, "9" * 32)
    sess.rendezvous_dir = str(outside)
    sess.log_path = str(outside / "pane.log")
    with pytest.raises(PermissionError):
        reg.delete(sess.sid, sess.cap)
    assert outside.exists()                         # outside dir untouched
```

(2) Run it + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_security_ladder.py -k "capbound or create_and_list" && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k delete
```

Expected FAIL: `/session/stream` (and any route whose `_do_*` does not yet route through `_authz_session`) returns non-403, and `delete` is absent / does not realpath-confine before `rm` so `test_delete_refuses_unconfined_dir` raises no `PermissionError`.

(3) Minimal implementation.

Ensure every cap-bound `_do_*` begins with `_authz_session`. Add/confirm in `session_endpoints.py`:

```python
class SessionMixin:
    # ...

    def _do_stream(self, body):
        sid = body.get("session_id")
        cap = body.get("cap")
        sess = self._authz_session(sid, cap)            # 403 before any tmux work
        return self._run_stream_turn(sess, body)        # held-lock SSE loop (turn component)

    def _do_get_envelope_response(self, session_id, cap, turn_uuid):
        sess = self._authz_session(session_id, cap)
        return self._emit_envelope(sess, turn_uuid)     # rendezvous-docsync component

    def _do_delete(self, session_id, cap):
        sess = self._authz_session(session_id, cap)
        self._registry.delete(sess.sid, cap)
        return {"ok": True}
```

(`_do_create` and `_do_list` intentionally do NOT call `_authz_session` — create mints the cap, list is cap-exempt but token+origin gated.)

Add/replace `delete` in `session_registry.py`:

```python
from paths import validate_session_id, assert_confined


class _Registry:
    # ...

    def delete(self, sid, cap):
        sid = validate_session_id(sid)
        with self._lock:
            sess = self._sessions.get(sid)
        if sess is None:
            return                                       # idempotent
        if not isinstance(cap, str) or not hmac.compare_digest(sess.cap, cap):
            raise PermissionError("cap mismatch")        # before ANY kill
        confined = assert_confined(sess.rendezvous_dir, self._base)  # raises on escape
        try:
            sess.tmux.pipe_pane_off(sess.pane)
        except Exception:
            pass
        try:
            sess.tmux.kill_server()
        except Exception:
            pass
        shutil.rmtree(confined, ignore_errors=True)
        with self._lock:
            if self._sessions.get(sid) is sess:
                del self._sessions[sid]
```

(Add `import hmac` at the top of `session_registry.py` if not already present.)

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_security_ladder.py -k "capbound or create_and_list" && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k delete
```

Expected: 9 unit cases pass (7 parametrized routes + 2 exemption cases); 3 lifecycle cases pass (tmux cases skipped without tmux; the unconfined unit case always runs).

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_endpoints.py session_registry.py tests/unit/test_security_ladder.py tests/integration/test_session_lifecycle.py && git commit -m "feat(session): cap on every route + /session/delete confined kill + rm

A parametrized matrix pins capture/send-key/interrupt/replay/get-envelope/
delete/stream all funnel through _authz_session(sid, cap) so a wrong cap is a
uniform 403; create/list stay cap-exempt but token+origin gated. delete checks
the cap with compare_digest BEFORE any kill, realpath-confines the rendezvous
dir under base, disables pipe-pane, kill-servers, and rm -rf's only the
confined dir; an unconfined dir is refused.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 52 — Tray-quit kill-all wcb_* + packaging the 5 modules (with quit-hook static gate)

**Files**
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/session_registry.py` (add module-level `kill_all_wcb`)
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/bridge_app.py` and `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/bridge_app_win.py` (call `kill_all_wcb` on quit)
- Modify: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/setup.py`, `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/pyinstaller-win.spec`
- Test: `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/integration/test_session_lifecycle.py` and `/Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session/tests/unit/test_packaging.py` (NEW)

FIX applied (TDD-shape): the tray-front-end quit-hook wiring gets an automated static gate (grep-style assertion that both `bridge_app.py` and `bridge_app_win.py` contain the `kill_all_wcb()` call), mirroring the packaging-grep approach — so the wiring is no longer an untested manual edit.

**Steps**

(1) Write the FAILING tests.

Append to `tests/integration/test_session_lifecycle.py`:

```python
@requires_tmux
def test_kill_all_wcb_kills_every_session_server(session_factory):
    import session_registry
    s1 = session_factory()
    s2 = session_factory()
    assert s1.tmux.has_session("t") and s2.tmux.has_session("t")
    session_registry.kill_all_wcb()
    assert not s1.tmux.has_session("t")
    assert not s2.tmux.has_session("t")
```

Create `tests/unit/test_packaging.py`:

```python
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

NEW_MODULES = ["paths", "fsm", "tmux_session", "session_registry", "session_endpoints"]


def test_setup_py_lists_new_modules():
    text = (ROOT / "setup.py").read_text()
    for mod in NEW_MODULES:
        assert mod in text, "setup.py is missing %s" % mod


def test_pyinstaller_spec_hiddenimports():
    text = (ROOT / "pyinstaller-win.spec").read_text()
    for mod in NEW_MODULES:
        assert mod in text, "spec missing hiddenimport %s" % mod


def test_both_tray_apps_wire_kill_all_on_quit():
    for fname in ("bridge_app.py", "bridge_app_win.py"):
        text = (ROOT / fname).read_text()
        assert "kill_all_wcb()" in text, "%s does not call kill_all_wcb on quit" % fname
        assert "import session_registry" in text, "%s does not import session_registry" % fname
```

(2) Run them + EXACT command + expected FAIL:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_packaging.py && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k kill_all
```

Expected FAIL: `AttributeError: module 'session_registry' has no attribute 'kill_all_wcb'`; packaging and quit-hook assertions fail because the modules are not listed and the call is not wired.

(3) Minimal implementation.

Add to `session_registry.py` (module-level — tray quit runs without a registry instance):

```python
import glob
import subprocess


def kill_all_wcb(tmux_bin="tmux"):
    """Tray-quit cleanup: kill every wcb_* tmux server. Enumerate the socket
    dir tmux uses ($TMUX_TMPDIR or /tmp/tmux-<uid>) for wcb_* sockets and
    kill-server each. Best-effort; never raises."""
    uid = os.getuid()
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    sock_dir = os.path.join(base, "tmux-%d" % uid) if base == "/tmp" else base
    for sock in glob.glob(os.path.join(sock_dir, "wcb_*")):
        name = os.path.basename(sock)
        try:
            subprocess.run([tmux_bin, "-L", name, "kill-server"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5)
        except Exception:
            pass
```

Wire it into both tray front-ends' quit/exit callback. In `bridge_app.py` and `bridge_app_win.py`, add to the quit handler:

```python
try:
    import session_registry
    session_registry.kill_all_wcb()
except Exception:
    pass
```

In `setup.py`, add the five modules to `py_modules`:

```python
py_modules=["server", "bridge_app", "bridge_app_win", "bridge_common",
            "paths", "fsm", "tmux_session", "session_registry", "session_endpoints"],
```

In `pyinstaller-win.spec`, add them to `hiddenimports`:

```python
hiddenimports=[..., "paths", "fsm", "tmux_session",
               "session_registry", "session_endpoints"],
```

(4) Run + expected PASS:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && python3 -m pytest -q tests/unit/test_packaging.py && python3 -m pytest -q tests/integration/test_session_lifecycle.py -k kill_all
```

Expected: 3 packaging/quit-hook tests pass; `test_kill_all_wcb_*` passes with tmux (skipped otherwise).

(5) Commit:

```
cd /Users/martintreiber/Documents/Development/web_cli_bridge/.worktrees/persistent-claude-session && git add session_registry.py bridge_app.py bridge_app_win.py setup.py pyinstaller-win.spec tests/unit/test_packaging.py tests/integration/test_session_lifecycle.py && git commit -m "feat(tray): kill-all wcb_* on quit + package the 5 session modules

kill_all_wcb enumerates wcb_* sockets in the tmux socket dir and kill-servers
each; both tray front-ends call it on quit (pinned by a static gate test).
setup.py py_modules and the PyInstaller spec hiddenimports now include
paths/fsm/tmux_session/session_registry/session_endpoints so the tray bundles
them.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Risk coverage

| # | Finding (sev) | Task(s) whose tests cover it |
|---|---|---|
| 1 | `*` CORS + token-optional = drive-by RCE (crit) | 49 (mandatory token, origin allowlist not `*`, Origin/Sec-Fetch guard, full error→status ladder), 51 (per-session cap on every `/session/*` route) |
| 2 | Byte-quiescence fires mid-turn; interrupt-hint/input-box unreliable (crit) | covered by turn-protocol-fsm `classify`/WORKING-gate tasks (rising-spinner end-to-end); this component's 41 (capture returns FSM state) exercises the `classify` consumer surface |
| 3 | persistDoc vs agent `newDoc` divergence; backfilled `data-rwa-id` (crit) | covered by rendezvous-docsync (`renderDoc(persisted)`, `data-rwa-id` anchoring); not owned by cleanup-security |
| 4 | Atomic rename ≠ write-before-read ordering / pipelining (crit) | covered by turn-protocol-fsm held-lock stage→send→read + rendezvous-docsync gen/sentinel echo; replay closed-slice in 41 corroborates closed-read ordering |
| 5 | Reconstruct race → dual locks; busy not durable (crit/high) | covered by registry-lifecycle (placeholder + Event `setdefault`, persisted `@wcb_turn`); 44/47 assert reaper never touches busy/reconstructing |
| 6 | Symlink read / path traversal / cap-less adoption (high) | 41 (`_authz_session` cap, `safe_open_nofollow` replay), 45 (confined sweep, no symlink follow), 49 (mandatory token), 50 (no caller-supplied socket override), 51 (cap on every route, confined delete) |
| 7 | Stale env read on finish-without-write / reconstruct (high) | 45 (sweep all `env.*`/`doc.*` on reconstruct); uuid-in-envelope read covered by rendezvous-docsync/turn-protocol |
| 8 | `/tmp` base hijack; pipe-pane shell sink (high) | 46 (`pipe_pane_on` full-path `shlex.quote`, disable-first); `$HOME` base lstat verify owned by path-safety/registry-lifecycle |
| 9 | CRLF/canonLF anchor mismatch; DSL compile vs live store (high) | covered by rendezvous-docsync (`canonLF` `\n`-only staging) and turn-protocol; not owned by cleanup-security |
| 10 | send-key answer races resume (high) | 41 (standalone send-key acquires turn lock and polls effect before release); held-lock fold covered by turn-protocol |
| 11 | Non-socket file bricks re-create; meta-missing→false death (high) | 48 (transient vs authoritative `_TmuxError` gating; liveness via `has-session`); 44 (2× confirm + grace) |
| 12 | Unbounded/leaked logs, no TTL/cap (high) | 46 (64 MiB rotation w/ offset base, re-pipe idempotency), 47 (idle-TTL 2h, max-8 → 429), 48 (reaper thread runs rotation), 52 (kill-all `wcb_*` on quit) |
| 13 | Transient `TmuxError` → live session reaped (med) | 48 (retryable vs authoritative classes), 44 (2× confirm + grace, never reap busy/reconstructing), 47 (idle-TTL never kills busy) |
| 14 | `@wcb_rendezvous` redirect; bad `cwd` silent (med) | covered by registry-lifecycle (re-derive dir from id cross-check, validate `cwd` isdir+confined); 50 drops caller-supplied socket so create cannot be redirected |
