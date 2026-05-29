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


STATES = (
    "starting", "thinking", "streaming", "awaiting_input",
    "idle", "idle_no_envelope", "dead",
)

# Composer readiness marker: the framed prompt line begins with '❯ '.
_COMPOSER_PROMPT = "❯"


def _spinner_timer(footer_or_screen: str):
    """Highest elapsed-timer value present, or None. Verb is ignored."""
    vals = [int(m) for m in SPINNER_TIMER_RE.findall(footer_or_screen)]
    return max(vals) if vals else None


def classify(screen: str, footer: str, env_present: bool,
             *, prev_timer: "int | None" = None,
             composer_seen: bool = False) -> "tuple[str, dict]":
    """Return (state, meta). PINNED SIGNATURE — callers pass composer_seen=.

    SCREEN-ONLY working signal — env is NOT a standalone working signal here:
    WORKING (thinking/streaming) iff a rising SPINNER_TIMER_RE OR FOOTER_WORKING
    is present. Once settled, env_present splits idle (file present) vs
    idle_no_envelope (file absent). "env absent => keep waiting" is the turn
    loop's file-edge concern (await_envelope), not classify's — otherwise
    idle_no_envelope would be unreachable and the loop could never settle. The
    spinner verb is NEVER a gate (randomized).
    """
    timer = _spinner_timer(screen)
    meta = {"timer": timer}

    # awaiting_input: a recognized menu/trust block takes precedence over the
    # WORKING signals (the caller must be able to answer it). Detect only the
    # explicit, calibrated blocks — never a generic 'input box present'.
    if TRUST_PROMPT in screen or TRUST_OPTION_YES in screen:
        meta["kind"] = "trust"
        meta["screen"] = screen
        return "awaiting_input", meta

    working = False
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
