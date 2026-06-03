"""Python port of the rwa's `parseBridgeEnvelope` (../rewritable, seeds/
rewritable.html). The rwa pulls the FIRST balanced top-level JSON object out of
the bridge's stdout and treats it as the `rwa-edit/1` envelope.

Used by the drop-in comparison so BOTH paths are checked with the exact
extraction logic the real caller uses: the legacy /run path returns the envelope
embedded in claude's chatty stdout (must be extracted), while /session/get-
envelope returns clean JSON bytes (parses directly). Either way, the rwa runs
this function and must get an equivalent object.
"""
import json
import re

# Mirror the rwa's leading/trailing markdown-fence stripping (real claude often
# wraps the envelope in a ```json fence).
_FENCE_LEAD = re.compile(r"^```(?:json|html)?\s*", re.IGNORECASE)
_FENCE_TRAIL = re.compile(r"```\s*$", re.IGNORECASE)


def parse_bridge_envelope(text):
    """Faithful port of the rwa's parseBridgeEnvelope (seeds/rewritable.html).

    Strips a leading/trailing code fence, takes the FIRST balanced top-level
    JSON object (brace-matched with string/escape awareness so braces inside a
    string value don't unbalance the scan), and returns it ONLY if it has a
    string `tool` and a truthy `envelope`. Otherwise returns None and STOPS — it
    does NOT keep scanning for a later object — exactly like the rwa returning
    null. Keeping this identical is what makes the drop-in comparison meaningful.
    """
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    text = _FENCE_TRAIL.sub("", _FENCE_LEAD.sub("", text.strip()), count=1)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return None
                    if isinstance(obj, dict) and isinstance(obj.get("tool"), str) \
                            and obj.get("envelope"):
                        return obj
                    return None          # first object invalid -> null and STOP
    return None
