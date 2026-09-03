"""Counters for the anomalies, as entities.

The JSONL on disk answers "what exactly happened at 04:06"; these answer "is
this getting worse", which is the question you actually ask day to day. Being
entities they are recorded, so they graph, they can drive an automation, and
they survive far longer than the files do.

One counter per kind rather than a single total. A NAK is the inverter
declining, a truncation is the serial leg mangling a frame, a CRC failure is a
frame arriving whole but wrong, and "no response" is the transport giving up —
different causes, different fixes. A merged counter would only tell you that
something is wrong, which you already knew.

Counts are cumulative since the first ever start: restored from the previous
state on restart, because a counter that resets on every reboot cannot answer
the only question it exists for.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .. import diag_hub

_LOGGER = logging.getLogger(__name__)

#: (kind, friendly name, icon). Mirrors the kinds emitted by the coordinator.
COUNTERS = (
    ("freeze", "Freezes", "mdi:snowflake"),
    ("rejected", "NAKs rejected", "mdi:hand-back-left-off"),
    ("truncated", "Truncated frames", "mdi:content-cut"),
    ("crc", "CRC failures", "mdi:alert-octagon-outline"),
    ("no_response", "No response", "mdi:timer-sand-empty"),
    ("raised", "Transport errors", "mdi:lan-disconnect"),
    ("unavailable", "Sections gone dark", "mdi:eye-off"),
)


class _AnomalyEntity(RestoreEntity, SensorEntity):
    """Shared plumbing: attach to the anomaly channel for this entity's life."""

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore()
        diag_hub.subscribe_anomalies(self._on_anomaly)

    async def async_will_remove_from_hass(self) -> None:
        diag_hub.unsubscribe_anomalies(self._on_anomaly)
        await super().async_will_remove_from_hass()

    async def _async_restore(self) -> None:
        raise NotImplementedError

    @callback
    def _on_anomaly(self, event: dict) -> None:
        raise NotImplementedError


class AnomalyCounter(_AnomalyEntity):
    """Cumulative count of one kind of anomaly, broken down by command."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, kind: str, name: str, icon: str) -> None:
        self._kind = kind
        self._count = 0
        self._per_command: dict[str, int] = {}
        self._attr_name = f"DESS anomalies {name}"
        self._attr_unique_id = f"dess_monitor_local_anomaly_{kind}"
        self._attr_icon = icon
        self._attr_native_value = 0

    async def _async_restore(self) -> None:
        last = await self.async_get_last_state()
        if last is None or last.state in (None, "unknown", "unavailable"):
            return
        try:
            self._count = int(float(last.state))
        except (TypeError, ValueError):
            return
        per_command = last.attributes.get("per_command")
        if isinstance(per_command, dict):
            try:
                self._per_command = {k: int(v) for k, v in per_command.items()}
            except (TypeError, ValueError):
                self._per_command = {}
        self._attr_native_value = self._count

    @property
    def extra_state_attributes(self) -> dict:
        return {"per_command": dict(self._per_command)}

    @callback
    def _on_anomaly(self, event: dict) -> None:
        if event.get("kind") != self._kind:
            return
        self._count += 1
        cmd = event.get("cmd") or "?"
        self._per_command[cmd] = self._per_command.get(cmd, 0) + 1
        self._attr_native_value = self._count
        self.async_write_ha_state()


class LastAnomalySensor(_AnomalyEntity):
    """When the last anomaly happened, and what it was."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self) -> None:
        self._attr_name = "DESS anomalies last event"
        self._attr_unique_id = "dess_monitor_local_anomaly_last"
        self._attr_icon = "mdi:clock-alert-outline"
        self._attr_native_value = None
        self._detail: dict = {}

    async def _async_restore(self) -> None:
        last = await self.async_get_last_state()
        if last is None or last.state in (None, "unknown", "unavailable"):
            return
        try:
            self._attr_native_value = datetime.fromisoformat(last.state)
        except (TypeError, ValueError):
            return
        self._detail = {
            k: v for k, v in last.attributes.items()
            if k in ("kind", "cmd", "detail")
        }

    @property
    def extra_state_attributes(self) -> dict:
        return dict(self._detail)

    @callback
    def _on_anomaly(self, event: dict) -> None:
        ts = event.get("ts")
        self._attr_native_value = (
            datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        )
        self._detail = {
            "kind": event.get("kind"),
            "cmd": event.get("cmd"),
            "detail": event.get("detail"),
        }
        self.async_write_ha_state()


def create_anomaly_sensors() -> list[SensorEntity]:
    """The full set. Created once per HA instance, not once per entry."""
    return [AnomalyCounter(k, n, i) for k, n, i in COUNTERS] + [
        LastAnomalySensor()
    ]
