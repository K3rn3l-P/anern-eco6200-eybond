"""CRC validation against frames captured from a live ANERN ECO-6200.

The decoders only check length where a section has a known field count, which
leaves QMOD, QPIGS2, QPIRI and QFWS unprotected — and QMOD is what a controller
reads to decide the operating regime. A CRC covers all of them without knowing
anything about their shape.
"""
from __future__ import annotations

import pytest

from custom_components.dess_monitor_local.api.crc import (
    validate_voltronic_response,
)

from real_frames import GOOD_FRAMES, TRUNCATED_FRAMES


def _body(frame: bytes) -> bytes:
    """The adapter's view of a frame: everything before the trailing CR."""
    body, _, _ = frame.partition(b"\r")
    return body


class TestRealFrames:
    @pytest.mark.parametrize("frame", GOOD_FRAMES)
    def test_good_frames_accepted(self, frame):
        assert validate_voltronic_response(_body(frame))[0]

    @pytest.mark.parametrize("frame", TRUNCATED_FRAMES)
    def test_truncated_frames_rejected(self, frame):
        assert not validate_voltronic_response(_body(frame))[0]

    def test_nak_has_a_valid_crc(self):
        # A NAK is a well-formed frame carrying no data, so the CRC cannot
        # catch it. That is what the coordinator's NAK handling is for: the
        # two checks are orthogonal and both are needed.
        assert validate_voltronic_response(_body(b"(NAKss\r"))[0]


class TestCarriageReturnContract:
    """The regression this file exists to prevent.

    validate_voltronic_response takes the response *without* its trailing CR.
    elfin_tcp splits on CR before calling it; the EyBond transport hands back
    the CR-terminated frame. Passing that straight through rejects every good
    frame — on a live plant that freezes every section on every cycle, then
    flips them unavailable.
    """

    def test_including_the_cr_breaks_every_good_frame(self):
        survivors = [f for f in GOOD_FRAMES if validate_voltronic_response(f)[0]]
        assert survivors == [], (
            "a CR-terminated frame validated: the contract changed, and the "
            "EyBond adapter must be revisited"
        )

    def test_stripping_the_cr_accepts_every_good_frame(self):
        assert all(validate_voltronic_response(_body(f))[0] for f in GOOD_FRAMES)
