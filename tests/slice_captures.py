#!/usr/bin/env python3
"""Slice the real claude calibration screens out of a capture log into the
per-state fixtures the FSM tests are driven by.

Source log default: docs/plans/2026-05-29-calibration-captures.log (claude
2.1.156, captured 2026-05-29). Panel headers have the exact form:

    ===== <name>  (<hh:mm:ss>)  alt=0  cmd=<proc> =====

Each fixture is the verbatim body between one header and the next (header
line itself dropped, trailing blank lines stripped). Re-run to regenerate:

    python3 tests/slice_captures.py
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
DEFAULT_SRC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "docs" / "plans" / "2026-05-29-calibration-captures.log"
)


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
    src = pathlib.Path(argv[1]) if len(argv) > 1 else DEFAULT_SRC
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
