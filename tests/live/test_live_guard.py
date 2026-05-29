"""The live tier must be opt-in: skipped unless WCB_LIVE_SMOKE=1, so the
default `python3 -m pytest -q` never spends claude quota. The real
test_live_smoke.py replaces this in the final phase; this only proves the
gate works and the live/ package is discoverable.
"""
import os

import pytest

live_only = pytest.mark.skipif(
    os.environ.get("WCB_LIVE_SMOKE") != "1",
    reason="live tier opt-in: set WCB_LIVE_SMOKE=1",
)


@live_only
def test_live_gate_runs_only_when_opted_in():
    # If this body ever executes, the env gate was honored.
    assert os.environ.get("WCB_LIVE_SMOKE") == "1"


def test_live_package_is_collectable():
    # Always runs: proves tests/live is a discoverable package.
    assert True
