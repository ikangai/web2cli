"""Pure state classifier for the claude TUI. No tmux, no HTTP.

Driven entirely by captured `tmux capture-pane -p` screens. Constants pinned
to claude v2.1.156 per the design's calibration section. The FOOTER_IDLE token
is the single canonical idle token also emitted by the canonical
tests/fake_claude.sh (Task 3, test-scaffold) — do not diverge.
"""
import re

# --- CALIBRATE constants (claude v2.1.156) ------------------------------------
FOOTER_WORKING = "esc to interrupt"                                 # WORKING gate
FOOTER_IDLE = "⏵⏵ bypass permissions on (shift+tab to cycle)"  # IDLE gate
TRUST_PROMPT = "Is this a project you created or one you trust?"
TRUST_OPTION_YES = "Yes, I trust this folder"
MARKER_ASSISTANT = "⏺"      # ⏺ assistant line / tool call
MARKER_RESULT = "⎿"         # ⎿ tool result
SPINNER_TIMER_RE = re.compile(r"\((\d+)s[ ·]")   # rising elapsed timer; verb ignored
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
    '─'). The footer is everything after the LAST such rule. If no rule
    is present, fall back to the trailing non-blank lines.
    """
    lines = screen.split("\n")
    last_rule = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s and set(s) <= {"─"}:
            last_rule = i
    if last_rule >= 0:
        tail = lines[last_rule + 1:]
    else:
        tail = lines
    footer = "\n".join(tail).strip()
    return footer
