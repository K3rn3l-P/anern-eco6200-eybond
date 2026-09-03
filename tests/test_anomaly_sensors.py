"""The anomaly counters.

The JSONL answers "what exactly happened at 04:06"; these answer "is this
getting worse", which is the question actually asked day to day. Being
entities they are recorded, so they graph and can drive an automation.
"""
from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local.sensors.anomaly_sensor import (  # noqa: E402
    COUNTERS,
    AnomalyCounter,
    create_anomaly_sensors,
)


class _Counter(AnomalyCounter):
    """Counts without a hass instance behind it."""

    def async_write_ha_state(self):  # noqa: D102 - test double
        pass


class TestCounter:
    def test_counts_only_its_own_kind(self):
        c = _Counter("truncated", "Truncated frames", "mdi:content-cut")
        c._on_anomaly({"kind": "truncated", "cmd": "QPIGS"})
        c._on_anomaly({"kind": "freeze", "cmd": "QPIGS"})
        c._on_anomaly({"kind": "truncated", "cmd": "QPIWS"})
        assert c.native_value == 2

    def test_breaks_down_by_command(self):
        # Which command is failing is the first thing you want to know: a
        # truncation on QMOD moves the operating regime, one on QPIRI does not.
        c = _Counter("truncated", "Truncated frames", "mdi:content-cut")
        for cmd in ("QPIGS", "QPIGS", "QMOD"):
            c._on_anomaly({"kind": "truncated", "cmd": cmd})
        assert c.extra_state_attributes["per_command"] == {"QPIGS": 2, "QMOD": 1}

    def test_missing_command_does_not_lose_the_count(self):
        c = _Counter("freeze", "Freezes", "mdi:snowflake")
        c._on_anomaly({"kind": "freeze"})
        assert c.native_value == 1
        assert c.extra_state_attributes["per_command"] == {"?": 1}


class TestSet:
    def test_every_emitted_kind_has_a_counter(self):
        emitted = {
            "raised", "rejected", "truncated", "crc",
            "no_response", "freeze", "unavailable",
        }
        assert {k for k, _, _ in COUNTERS} == emitted

    def test_unique_ids_are_distinct(self):
        ids = [s.unique_id for s in create_anomaly_sensors()]
        assert len(ids) == len(set(ids))

    def test_includes_a_last_event_sensor(self):
        assert len(create_anomaly_sensors()) == len(COUNTERS) + 1
