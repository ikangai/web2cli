"""parse_bridge_envelope must faithfully mirror the rwa's parseBridgeEnvelope
(seeds/rewritable.html) so the drop-in comparison is meaningful: strip a leading/
trailing code fence, take the FIRST balanced top-level object, and accept it only
if it has a string `tool` and a truthy `envelope` — otherwise return None and
STOP (do not keep scanning)."""
from bridge_envelope import parse_bridge_envelope


def test_extracts_first_balanced_object_from_prose():
    s = 'Here you go:\n{"tool":"apply_edits","envelope":{"version":"rwa-edit/1"}}\nDone.'
    o = parse_bridge_envelope(s)
    assert o["tool"] == "apply_edits"


def test_braces_inside_strings_do_not_unbalance():
    s = '{"tool":"replace_document","envelope":{"doc":"a{b}c","reason":"x"}}'
    o = parse_bridge_envelope(s)
    assert o["envelope"]["doc"] == "a{b}c"


def test_strips_json_code_fence_like_the_rwa():
    s = '```json\n{"tool":"apply_edits","envelope":{"version":"rwa-edit/1"}}\n```'
    o = parse_bridge_envelope(s)
    assert o["tool"] == "apply_edits"


def test_first_object_without_tool_or_envelope_returns_none_and_stops():
    # The rwa returns null and STOPS — it does not skip to a later valid object.
    s = '{"note":"hi"} {"tool":"apply_edits","envelope":{"x":1}}'
    assert parse_bridge_envelope(s) is None


def test_no_json_object_returns_none():
    assert parse_bridge_envelope("no json here at all") is None
