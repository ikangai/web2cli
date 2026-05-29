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
#   WCB_FAKE_TIMER=1   emit a rising spinner timer `(<N>s · thinking)`
#
# Like real claude this is a full-screen TUI: the composer/footer is REDRAWN in
# place pinned at the bottom of the pane, while transcript lines (spinner work,
# `⏺ Write`, `⏺ DONE`) scroll above it. So a `capture-pane -p` only ever shows
# the *current* footer -- a stale WORKING footer never lingers after a turn,
# which is exactly the edge the bridge's idle/working discrimination relies on.

set -u

FOOTER_PREFIX='  ⏵⏵ bypass permissions on (shift+tab to cycle)'
FOOTER_IDLE="${FOOTER_PREFIX} · ← for agents"
FOOTER_WORKING="${FOOTER_PREFIX} · esc to interrupt"
RULE='────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────'
DELAY="${WCB_FAKE_DELAY:-0.3}"
RESUME_UUID='8411fd46-093b-4fdd-9ae0-183cfa5ba98b'

# The composer block is RULE / prompt / RULE / footer == 4 rows; pin it at the
# bottom so transcript output scrolls above it.
FOOTER_ROWS=4

# cleanup-security's interrupt corroboration looks for this marker.
trap 'printf "%s\n" "^C INTERRUPTED"' INT

# Redraw the 4-row composer block pinned at the bottom of the pane: save the
# cursor, jump to the footer region, clear it and everything below, paint, then
# restore the cursor so the next transcript line lands above the footer. We
# clamp to the last row with row 999 (the terminal pins it to the real bottom)
# and step up, so this is independent of the actual pane height.
draw_composer() {
    local footer="$1"
    printf '\033[s'                               # save cursor
    printf '\033[999;1H'                          # clamp to the last pane row
    printf '\033[%dA' "$((FOOTER_ROWS - 1))"      # up to the first footer row
    printf '\r'
    printf '\033[J'                               # clear from here to end of pane
    printf '%s\n' "$RULE"
    printf '%s\n' '❯ '
    printf '%s\n' "$RULE"
    printf '%s' "$footer"
    printf '\033[u'                               # restore cursor
}

print_trust() {
    printf '%s\n' ' Quick safety check: Is this a project you created or one you trust?'
    printf '%s\n' ' ❯ 1. Yes, I trust this folder'
    printf '%s\n' '   2. No, exit'
    printf '%s\n' ' Enter to confirm · Esc to cancel'
}

clear

if [ "${WCB_FAKE_TRUST:-}" = "1" ]; then
    print_trust
    # Block until the test answers ["1","Enter"]; we ignore the value.
    IFS= read -r _trust_answer || true
    clear
fi

draw_composer "$FOOTER_IDLE"

while IFS= read -r line; do
    case "$line" in
        WRITE\ *)
            # WRITE <abs_env_path> <turn_uuid>
            rest="${line#WRITE }"
            env_path="${rest%% *}"
            turn_uuid="${rest#* }"
            draw_composer "$FOOTER_WORKING"
            if [ "${WCB_FAKE_TIMER:-}" = "1" ]; then
                # Rising spinner elapsed timer (matches real `✻ Smooshing… (Ns …)`).
                for n in 1 2 3; do
                    printf '%s\n' "✻ Smooshing… (${n}s · thinking)"
                    draw_composer "$FOOTER_WORKING"
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
            draw_composer "$FOOTER_IDLE"
            ;;
        EXIT)
            # Match the real post-exit screen: resume hint then shell prompt.
            # Settle one slice so the hint is capturable before the pane (and
            # thus the session) is torn down by the exiting process.
            printf '%s\n' 'Resume this session with:'
            printf '%s\n' "claude --resume ${RESUME_UUID}"
            printf '%s\n' 'martintreiber@10 fake %'
            sleep "$DELAY"
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
