#!/usr/bin/env bash
# Dual-mode claude mimic for the DROP-IN comparison (claude -p vs the tmux
# session path). It emits the SAME real `rwa-edit/1` envelope through BOTH:
#
#   -p / --print mode : reads the prompt on stdin (like `claude -p`), prints the
#                       envelope JSON to stdout, exits. Drives the legacy
#                       POST /run + `claude -p` path.
#   TUI mode (default): full-screen composer; on the `WRITE <env> <uuid>`
#                       handshake it writes that envelope (plus turn_uuid/gen)
#                       to env.<uuid>.json via .part->rename. Drives the new
#                       POST /session/stream + /session/get-envelope path.
#
# The envelope is selected by WCB_DROPIN_SCENARIO (apply_edits |
# replace_document | apply_dsl_plan) so both paths produce a byte-identical
# tool+envelope payload — the assertion that the bridge is a faithful drop-in.
#
# This is a SEPARATE fixture from the canonical tests/fake_claude.sh (which is
# never re-authored); it borrows that file's calibration-pinned TUI scaffolding.

set -u

SCENARIO="${WCB_DROPIN_SCENARIO:-apply_edits}"

# Print the `"tool":...,"envelope":{...}` fragment (NO braces, NO turn_uuid/gen)
# so each mode can wrap it as it needs.
emit_core() {
    case "$SCENARIO" in
        apply_edits)
            printf '"tool":"apply_edits","envelope":{"version":"rwa-edit/1","edits":[{"find":"<h1>Old Title</h1>","replace":"<h1>New Title</h1>"}]}'
            ;;
        replace_document)
            printf '"tool":"replace_document","envelope":{"version":"rwa-edit/1","doc":"<html><body>fresh</body></html>","reason":"full rewrite"}'
            ;;
        apply_dsl_plan)
            printf '"tool":"apply_dsl_plan","envelope":{"version":"rwa-edit/1","plan":[{"op":"setText","id":"title","text":"Hello"}]}'
            ;;
        *)
            printf '"tool":"apply_edits","envelope":{"version":"rwa-edit/1","edits":[]}'
            ;;
    esac
}

# ----------------------------------------------------------------------------
# -p / --print one-shot mode (the `claude -p` analog)
# ----------------------------------------------------------------------------
if [ "${1:-}" = "-p" ] || [ "${1:-}" = "--print" ]; then
    cat >/dev/null 2>&1 || true       # consume the piped prompt like claude -p
    # Real claude is chatty: surround the JSON with prose so the comparison
    # also exercises parseBridgeEnvelope's first-balanced-object extraction.
    printf 'Here is the edit envelope you asked for:\n'
    printf '{'; emit_core; printf '}'
    printf '\nApplied successfully.\n'
    exit 0
fi

# ----------------------------------------------------------------------------
# TUI mode (the /session/* analog) — calibration-pinned composer scaffolding
# ----------------------------------------------------------------------------
FOOTER_PREFIX='  ⏵⏵ bypass permissions on (shift+tab to cycle)'
FOOTER_IDLE="${FOOTER_PREFIX} · ← for agents"
FOOTER_WORKING="${FOOTER_PREFIX} · esc to interrupt"
RULE='────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────'
DELAY="${WCB_FAKE_DELAY:-0.3}"
FOOTER_ROWS=4

trap 'printf "%s\n" "^C INTERRUPTED"' INT

draw_composer() {
    local footer="$1"
    printf '\033[s'
    printf '\033[999;1H'
    printf '\033[%dA' "$((FOOTER_ROWS - 1))"
    printf '\r'
    printf '\033[J'
    printf '%s\n' "$RULE"
    printf '%s\n' '❯ '
    printf '%s\n' "$RULE"
    printf '%s' "$footer"
    printf '\033[u'
}

clear
draw_composer "$FOOTER_IDLE"

while IFS= read -r line; do
    case "$line" in
        WRITE\ *)
            rest="${line#WRITE }"
            env_path="${rest%% *}"
            turn_uuid="${rest#* }"
            if ! [[ "$turn_uuid" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
                continue
            fi
            draw_composer "$FOOTER_WORKING"
            sleep "$DELAY"
            # Same envelope as -p mode, plus the freshness fields the bridge's
            # byte-exact read-back requires (turn_uuid + gen == turn_uuid).
            printf '{%s,"turn_uuid":"%s","gen":"%s"}' \
                "$(emit_core)" "$turn_uuid" "$turn_uuid" > "${env_path}.part"
            mv -f "${env_path}.part" "$env_path"
            printf '%s\n' '⏺ Write('"$env_path"')'
            printf '%s\n' '⏺ DONE'
            draw_composer "$FOOTER_IDLE"
            ;;
        EXIT)
            printf '%s\n' 'Resume this session with:'
            printf '%s\n' 'claude --resume 00000000-0000-0000-0000-000000000000'
            printf '%s\n' 'host fake %'
            sleep "$DELAY"
            exit 0
            ;;
        '')
            : ;;
        *)
            : ;;
    esac
done
