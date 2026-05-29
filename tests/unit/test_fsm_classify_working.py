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


def test_env_absent_settled_screen_is_idle_no_envelope():
    # RECONCILED (Task 20 vs 21 conflict): a SETTLED composer (no working
    # footer/timer) with the env file absent is idle_no_envelope — NOT working.
    # env-absent is NOT a standalone classify WORKING signal; "keep waiting for
    # the file" is the turn loop's file-edge concern (await_envelope, Task 25),
    # which treats idle_no_envelope as "settled by screen, no fresh file — poll
    # briefly". Making env-absent classify-working would render idle_no_envelope
    # unreachable and the loop unable to settle.
    composer = _screen("composer_ready.txt")
    footer = fsm.footer_of(composer)
    assert fsm.FOOTER_WORKING not in footer
    state, _ = fsm.classify(
        composer, footer, env_present=False, composer_seen=True
    )
    assert state == "idle_no_envelope"


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
        "╭─ Claude Code ─╮\n"
        "Smooshing… Baked for 5s thinking\n"
        "────────\n"
        "❯ \n"
        "────────\n"
        "  " + fsm.FOOTER_IDLE + "\n"
    )
    state, _ = fsm.classify(
        fake, fsm.footer_of(fake), env_present=True, composer_seen=True
    )
    assert state == "idle"  # the verb 'Smooshing…' did not gate WORKING
