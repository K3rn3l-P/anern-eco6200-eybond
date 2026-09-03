"""Which decoder errors count as a failed read.

QPIGS2 and QFWS are NAKed by design on this firmware, so a refusal from them
is the normal answer and must not burn a retry. The old test skipped the
decoder's error entirely for those two — which also skipped *corruption*: a
truncated QPIGS2 or QFWS counted as a successful read and left no log line, so
the falsified section was invisible in both the logs and the counters.
"""
from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local.coordinators.direct_coordinator import (  # noqa: E402
    DirectCoordinator,
    _classify,
)

_C = DirectCoordinator

NAK = "NAK response received. Command not accepted."
NULL = "null response received. Command not accepted."
EMPTY = "empty response"
SHORT_QPIGS = "QPIGS frame too short: 15 of 24 fields (need 21)"
SHORT_QPIWS = "QPIWS frame too short: 4 of 32 status bits"
CRC = "CRC mismatch"


class TestOrdinaryCommands:
    @pytest.mark.parametrize("err", [NAK, NULL, EMPTY, SHORT_QPIGS, CRC])
    def test_any_error_is_rejected(self, err):
        assert _C._is_rejected(_C, "QPIGS", err) is True

    def test_no_error_is_accepted(self):
        assert _C._is_rejected(_C, "QPIGS", None) is False
        assert _C._is_rejected(_C, "QPIGS", "") is False


class TestNakExpectedCommands:
    """QPIGS2 and QFWS: refusal tolerated, corruption not."""

    @pytest.mark.parametrize("cmd", ["QPIGS2", "QFWS"])
    @pytest.mark.parametrize("err", [NAK, NULL, EMPTY])
    def test_refusal_is_tolerated(self, cmd, err):
        # Rejecting these would freeze the section every cycle and flip it
        # unavailable after six — a regression, not a fix.
        assert _C._is_rejected(_C, cmd, err) is False

    @pytest.mark.parametrize("cmd", ["QPIGS2", "QFWS"])
    @pytest.mark.parametrize("err", [SHORT_QPIGS, SHORT_QPIWS, CRC])
    def test_corruption_is_still_rejected(self, cmd, err):
        # The hole being closed: these used to return False.
        assert _C._is_rejected(_C, cmd, err) is True

    @pytest.mark.parametrize("cmd", ["QPIGS2", "QFWS"])
    def test_clean_read_is_accepted(self, cmd):
        assert _C._is_rejected(_C, cmd, None) is False


class TestClassify:
    """Three kinds, not one bucket: different causes, different fixes."""

    def test_crc(self):
        assert _classify(CRC) == "crc"

    @pytest.mark.parametrize("err", [SHORT_QPIGS, SHORT_QPIWS])
    def test_truncated(self, err):
        assert _classify(err) == "truncated"

    @pytest.mark.parametrize("err", [NAK, NULL, EMPTY])
    def test_rejected(self, err):
        assert _classify(err) == "rejected"

    def test_none_is_a_plain_rejection(self):
        assert _classify(None) == "rejected"
