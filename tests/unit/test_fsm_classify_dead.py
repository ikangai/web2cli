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
