"""The FSM tests are driven by REAL claude screens, not synthetic ones.

This asserts the capture fixtures exist and each carries the pinned v2.1.156
token that the FSM will classify on. If the capture log is re-sliced,
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
    "turn_done.txt": "⎿  Wrote",
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
    # elapsed timer, not just the `esc to interrupt` footer.
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
