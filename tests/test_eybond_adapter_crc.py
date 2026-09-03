"""EyBondAdapter.get_data: where the CRC check lives, and why it lives there.

Deliberately in the adapter rather than the transport. send_eybond_bytes /
EybondManager.send_frame are shared with Modbus (a different CRC — it is the
path that writes the charger priority), with set commands, with PI18 and with
the debug panel's manual send. Validating there as Voltronic would reject
every Modbus reply.
"""
from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local.api.adapters import eybond  # noqa: E402
from custom_components.dess_monitor_local.api.adapters.eybond import (  # noqa: E402
    EyBondAdapter,
)

_URI = "eybond://0.0.0.0:8899/1"

GOOD = (
    b"(241.0 50.0 230.0 50.0 0414 0384 006 410 53.60 012 064 0061 10.1 "
    b"112.8 00.00 00000 00010110 00 00 01149 010\x19L\r"
)
# The same section truncated on the wire: cut mid-field, terminated by a high
# byte instead of the CR. Captured 1 Sep 2026 at 13:45.
TRUNCATED = (
    b"(241.6 49.9 229.9 50.0 0367 0279 005 413 53.80 014 066 0061 10.0 "
    b"111.4 00.00 00000 00010110 00 00 011\xff"
)


@pytest.fixture
def reply(monkeypatch):
    """Make the transport return a chosen frame, without touching the network."""

    def _set(frame):
        async def _fake(*args, **kwargs):
            return frame

        monkeypatch.setattr(eybond, "send_eybond_voltronic", _fake)

    return _set


class TestStrictCrcOn:
    @pytest.mark.asyncio
    async def test_good_frame_decodes(self, reply):
        reply(GOOD)
        d = await EyBondAdapter(_URI, 30, strict_crc=True).get_data("QPIGS")
        assert "error" not in d
        assert d["grid_voltage"] == "241.0"

    @pytest.mark.asyncio
    async def test_truncated_frame_is_rejected(self, reply):
        reply(TRUNCATED)
        d = await EyBondAdapter(_URI, 30, strict_crc=True).get_data("QPIGS")
        # An error, not {}: the coordinator logs the rejection, retries, then
        # freezes on last known data. An empty dict is dropped silently.
        assert d.get("error") == "CRC mismatch"

    @pytest.mark.asyncio
    async def test_rejection_is_a_non_empty_dict(self, reply):
        reply(TRUNCATED)
        assert await EyBondAdapter(_URI, 30, strict_crc=True).get_data("QPIGS")


class TestStrictCrcOff:
    @pytest.mark.asyncio
    async def test_truncated_frame_still_reaches_the_decoder(self, reply):
        reply(TRUNCATED)
        d = await EyBondAdapter(_URI, 30, strict_crc=False).get_data("QPIGS")
        assert d.get("error") != "CRC mismatch"
        assert "frame too short" in d.get("error", "")

    @pytest.mark.asyncio
    async def test_good_frame_decodes(self, reply):
        reply(GOOD)
        d = await EyBondAdapter(_URI, 30, strict_crc=False).get_data("QPIGS")
        assert d["grid_voltage"] == "241.0"


class TestNoResponse:
    @pytest.mark.asyncio
    async def test_empty_reply_is_not_a_crc_error(self, reply):
        # No response is a transport failure, not a corrupt frame. They need
        # different fixes, so they must stay distinguishable.
        reply(None)
        assert await EyBondAdapter(_URI, 30, strict_crc=True).get_data("QPIGS") == {}
