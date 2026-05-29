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
