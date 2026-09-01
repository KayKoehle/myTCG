"""What an online guest may ask its host to do (`webapp/js/guestcalls.js`).

Online play is host-authoritative and the host is another player's browser: it
relays each guest's API calls to its own instance, so this rule set is the only
thing between a player and the rest of the table's cards. The cases that matter
are not malformed requests but the ordinary payloads with one number changed —
`/api/state` builds a snapshot for the viewer it is asked for, and a viewer sees
their own hand.

`scripts/run_guest_calls.mjs` drives the rules directly; this audits its report.
Skipped when node is not on PATH — the module is only ever run by a browser or
by node, so there is nothing to fall back to.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run_guest_calls.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytest.fixture(scope="module")
def report() -> dict:
    result = subprocess.run(
        ["node", str(SCRIPT), "--json"],
        cwd=REPO, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def test_every_guest_call_rule_holds(report):
    broken = [
        f"{entry['label']}: {entry['detail']}" if entry["detail"] else entry["label"]
        for entry in report["results"] if not entry["ok"]
    ]
    assert not broken, "; ".join(broken)
