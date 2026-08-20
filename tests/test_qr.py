"""The QR pair: `webapp/js/qr.js` writes symbols, `webapp/js/qrdecode.js` reads
them, and neither is checked by eye.

A symbol with a subtly wrong format field looks perfectly plausible and scans as
nothing, and a decoder that works on the clean matrix it was handed says nothing
about one pointed at a phone screen. So the real check is a round trip through
something shaped like a photograph — tilted, rotated, blurred, unevenly lit and
noisy — which `scripts/run_qr_scan.mjs` synthesises and this audits.

Skipped when node is not on PATH; the JS is only ever run by a browser or by
node, so there is nothing to fall back to.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run_qr_scan.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytest.fixture(scope="module")
def report() -> dict:
    result = subprocess.run(
        ["node", str(SCRIPT), "--json"],
        cwd=REPO, capture_output=True, text=True, timeout=300, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_decoder_agrees_with_the_encoders_format_table(report):
    """The encoder tabulates the fifteen format bits by hand; the decoder derives
    them from the BCH code. One of the two being wrong is exactly the failure
    that produces a symbol nothing will read, so they are made to agree."""
    assert report["format_table_agrees"]


def test_every_version_the_encoder_can_write_reads_back(report):
    assert report["round_trip_failures"] == []


def test_error_correction_absorbs_real_damage(report):
    """A level-M symbol tolerates roughly 15% of its modules being wrong, and
    that tolerance is the whole reason a code scans from a hand-held phone. A
    decoder that only reads undamaged matrices would pass every other test
    here."""
    assert report["damage_tolerated"] >= 15


def test_it_reads_a_code_out_of_a_photograph(report):
    """Every condition a player will actually produce: the code small in frame,
    out of focus, lit from one side, held at an angle, off to one corner."""
    missed = [label for label, ok in report["pipeline"].items() if not ok]
    assert not missed, f"could not read: {', '.join(missed)}"


def test_the_hard_case_reads_often_enough_to_feel_instant(report):
    """Everything wrong at once — five-pixel modules, blurred, noisy, unevenly
    lit, rotated and tilted. A scanner gets frames by the dozen, so the bar is a
    decent share of them rather than all; half is a lock inside a second."""
    hard = report["hard_frames"]
    assert hard["decoded"] >= hard["trials"] // 2, hard


def test_a_frame_is_cheap_enough_to_scan_continuously(report):
    """The decoder runs on every frame the camera gives us, on a phone. Well
    under a video frame's worth of time, or the preview stutters and the player
    cannot aim."""
    assert report["decode_ms"] < 33
