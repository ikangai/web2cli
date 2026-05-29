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
    assert "⏵⏵ bypass permissions on (shift+tab to cycle)" in once


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
    assert "⏵⏵ bypass permissions on (shift+tab to cycle)" in footer
    assert "esc to interrupt" not in footer


def test_footer_of_empty_screen_is_empty_string():
    assert fsm.footer_of("") == ""
    assert fsm.footer_of("\n\n\n") == ""
