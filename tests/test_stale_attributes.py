"""The staleness attribute must not break entities that have no attributes.

Regression test for a live outage. Reading self._attr_extra_state_attributes
directly looks safe — HA's Entity documents it — but Entity does not define it
until a subclass sets one, and the CachedProperties metaclass mangles the
name. So on every entity that never set attributes (most of them) the read
raised AttributeError from inside async_write_ha_state, which HA catches per
entity: 58 of 91 entities went unavailable on a live plant while the
integration otherwise looked healthy.

A property added to a shared base class runs on every subclass, including the
ones that set nothing — and the test has to instantiate one of those.
"""
from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local.sensors.direct_sensor import (  # noqa: E402
    DirectTypedSensorBase,
)


class _Coord:
    def __init__(self, n=0):
        self._n = n

    def section_failures(self, device_id, section):
        return self._n


class _Device:
    inverter_id = "dev1"


class _Bare(DirectTypedSensorBase):
    """A sensor that never sets _attr_extra_state_attributes: the common case."""

    def __init__(self, coordinator, section="qpigs"):
        # Bypass the entity __init__: this exercises the property only.
        self.coordinator = coordinator
        self._inverter_device = _Device()
        self.data_section = section


class _WithAttrs(_Bare):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_extra_state_attributes = {"existing": "kept"}


class TestBareEntity:
    def test_does_not_raise(self):
        assert _Bare(_Coord()).extra_state_attributes is not None

    def test_reports_fresh(self):
        attrs = _Bare(_Coord(0)).extra_state_attributes
        assert attrs["stale"] is False
        assert attrs["stale_cycles"] == 0

    def test_reports_frozen(self):
        attrs = _Bare(_Coord(3)).extra_state_attributes
        assert attrs["stale"] is True
        assert attrs["stale_cycles"] == 3


class TestEntityWithItsOwnAttributes:
    def test_existing_attributes_survive(self):
        attrs = _WithAttrs(_Coord(1)).extra_state_attributes
        assert attrs["existing"] == "kept"
        assert attrs["stale"] is True

    def test_original_dict_is_not_mutated(self):
        e = _WithAttrs(_Coord(1))
        e.extra_state_attributes
        assert "stale" not in e._attr_extra_state_attributes
