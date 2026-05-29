import session_registry as reg

UUID = "12345678-1234-1234-1234-1234567890ab"
DOC = "/home/u/.web_cli_bridge/rendezvous/wcb_x/doc." + UUID + ".html"
ENV = "/home/u/.web_cli_bridge/rendezvous/wcb_x/env." + UUID + ".json"


def test_prompt_names_doc_and_env_and_part_rename():
    p = reg.build_turn_prompt("Make the title bold", DOC, ENV, UUID)
    assert DOC in p
    assert ENV in p
    assert ENV + ".part" in p
    # the explicit .part -> rename instruction (the completion edge)
    assert "rename" in p.lower()
    # freshness: Read it now, do not rely on memory
    assert "do not rely on memory" in p.lower()
    # gen + turn_uuid echo requirement
    assert UUID in p
    assert "rwa:gen" in p
    assert '"gen"' in p
    assert '"turn_uuid"' in p


def test_prompt_embeds_the_user_instruction():
    p = reg.build_turn_prompt("Translate to French", DOC, ENV, UUID)
    assert "Translate to French" in p


def test_prompt_is_lf_only():
    p = reg.build_turn_prompt("x", DOC, ENV, UUID)
    assert "\r" not in p
