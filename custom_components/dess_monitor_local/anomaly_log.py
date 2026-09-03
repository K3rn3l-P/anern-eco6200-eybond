"""Persist anomalies to disk, so they are there before anyone goes looking.

The evidence that the frame fixes work — a rejection, a freeze, a truncated
payload — used to exist only as DEBUG log lines, inside a rolling window
shared with the whole of Home Assistant, at a level that resets itself on
every restart. Roughly eighty minutes of history for events that show up a
handful of times a week.

This attaches a sink to ``diag_hub`` so the same events become JSONL on disk
plus, for the rare ones, a dump of the raw frame ring. Attaching a sink does
NOT switch on the hot-path instrumentation: ``diag_hub.active()`` stays false
unless a debug panel is open, so the transport's per-frame events cost nothing
as before.

Writing happens on the executor. The sink itself runs on the event loop, on
the polling path, so it only hands the event to a queue — a slow or full disk
must never stall a poll.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from homeassistant.core import HomeAssistant, callback

from . import diag_hub, frame_log

_LOGGER = logging.getLogger(__name__)

FOLDER = "dess_monitor_local"
RETENTION_DAYS = 30

#: Anomalies worth dumping the raw frame ring for: the ones whose cause is in
#: the bytes. A truncation or a CRC failure can only be diagnosed from them,
#: and a section going dark is worth the last twenty frames that led to it.
#:
#: Deliberately NOT "rejected". A NAK is a well-formed frame that declines, so
#: its bytes say nothing: seen on a live plant on 3 Sep 2026, that dump was 19
#: ordinary frames plus one "(NAKss" — 5.5 kB of evidence of nothing. Dumping
#: on every refusal just fills the folder.
_DUMP_KINDS = ("truncated", "crc", "unavailable")

#: Event fired on the HA bus for the rare kinds, so automations can react.
BUS_EVENT = "dess_monitor_local_anomaly"
_BUS_KINDS = ("truncated", "crc", "unavailable")

#: Bound on queued events. A burst that outruns the writer drops the excess
#: rather than growing without limit; the counters in the panel still show it.
_QUEUE_MAX = 500


def _stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S")


def write_event(base: Path, event: dict) -> None:
    """Append one event to today's JSONL file. Runs on the executor."""
    day = datetime.fromtimestamp(event.get("ts", time.time())).strftime("%Y-%m-%d")
    path = base / f"anomalies-{day}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def dump_frames(base: Path, command: str, ts: float) -> str | None:
    """Snapshot the raw frame ring for one command. Runs on the executor.

    Returns the file name, or None when the ring holds nothing for it — the
    ring lives in RAM and scrolls in minutes, so this has to happen at the
    moment of the anomaly, not when somebody gets around to looking.
    """
    frames = frame_log.snapshot().get(command)
    if not frames:
        return None
    name = f"frames-{command}-{_stamp(ts)}.json"
    (base / name).write_text(
        json.dumps(frames, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return name


def purge(base: Path, days: int) -> int:
    """Delete our own files older than ``days``. Runs on the executor.

    Deliberately scoped to the two name prefixes this module writes: a glob of
    everything would take a README, or anything else somebody leaves in the
    folder, with it.
    """
    cutoff = time.time() - days * 86400
    removed = 0
    for pattern in ("anomalies-*.jsonl", "frames-*.json"):
        for path in base.glob(pattern):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


# --------------------------------------------------------------------------
# Reading back — pure functions, so they can be tested against real captures
# --------------------------------------------------------------------------

#: Events closer together than this belong to the same incident. A dongle that
#: drops its TCP session fails every command queued behind it, so one transport
#: event surfaces as three or four anomalies seconds apart. Counting those
#: separately overstates the fault rate badly: on two days of real capture,
#: 133 events were 92 incidents.
EPISODE_WINDOW_S = 30.0


def read_events(base: Path, limit: int = 2000) -> list[dict]:
    """Load recent anomalies, oldest first. Runs on the executor.

    Reads whole files but returns at most ``limit`` events: the JSONL grows
    with every incident and the panel only ever renders the tail.
    """
    events: list[dict] = []
    for path in sorted(base.glob("anomalies-*.jsonl")):
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue  # a partially written tail line
        except OSError:
            continue
    events.sort(key=lambda e: e.get("ts", 0))
    return events[-limit:]


def group_episodes(
    events: list[dict], window: float = EPISODE_WINDOW_S
) -> list[list[dict]]:
    """Cluster events into incidents by arrival gap. Input must be sorted."""
    episodes: list[list[dict]] = []
    for event in events:
        ts = event.get("ts", 0)
        if episodes and ts - episodes[-1][-1].get("ts", 0) <= window:
            episodes[-1].append(event)
        else:
            episodes.append([event])
    return episodes


def summarise(events: list[dict]) -> dict:
    """Counts by kind and by command, plus the incident count."""
    by_kind: dict[str, int] = {}
    by_cmd: dict[str, int] = {}
    for event in events:
        kind = event.get("kind", "?")
        cmd = event.get("cmd", "?")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_cmd[cmd] = by_cmd.get(cmd, 0) + 1
    return {
        "events": len(events),
        "episodes": len(group_episodes(events)),
        "by_kind": by_kind,
        "by_cmd": by_cmd,
        "last_ts": events[-1].get("ts") if events else None,
    }


def list_dumps(base: Path) -> list[dict]:
    """The frame dumps on disk, newest first."""
    out = []
    for path in base.glob("frames-*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({"name": path.name, "size": stat.st_size, "ts": stat.st_mtime})
    out.sort(key=lambda d: d["ts"], reverse=True)
    return out


def read_dump(base: Path, name: str) -> dict:
    """One frame dump, by file name.

    The name comes from the browser, so it is checked against the directory
    listing rather than trusted — no path traversal, no reading outside the
    folder.
    """
    if name not in {d["name"] for d in list_dumps(base)}:
        return {"error": "unknown dump"}
    try:
        return {
            "name": name,
            "frames": json.loads((base / name).read_text(encoding="utf-8")),
        }
    except (OSError, ValueError) as err:
        return {"error": str(err)}


class AnomalyLog:
    """Bridges diag_hub anomalies onto disk."""

    def __init__(self, hass: HomeAssistant, base: Path) -> None:
        self._hass = hass
        self._base = base
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._worker: asyncio.Task | None = None
        self._dropped = 0

    @callback
    def _on_event(self, event: dict) -> None:
        """diag_hub sink. Runs on the loop, on the polling path — stay cheap."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._persist(event)
            except Exception:  # noqa: BLE001 — never kill the writer
                _LOGGER.exception("anomaly_log: failed to persist %s", event.get("t"))
            finally:
                self._queue.task_done()

    async def _persist(self, event: dict) -> None:
        record = dict(event)
        if event.get("kind") in _DUMP_KINDS and event.get("cmd"):
            name = await self._hass.async_add_executor_job(
                dump_frames, self._base, event["cmd"], event.get("ts", time.time())
            )
            if name:
                record["frames"] = name
        await self._hass.async_add_executor_job(write_event, self._base, record)
        if event.get("kind") in _BUS_KINDS:
            # Fired for the rare ones only, so an automation can react to a
            # corrupt frame or a section going dark without polling a file.
            self._hass.bus.async_fire(BUS_EVENT, record)

    async def async_start(self) -> None:
        await self._hass.async_add_executor_job(
            lambda: self._base.mkdir(parents=True, exist_ok=True)
        )
        diag_hub.subscribe_anomalies(self._on_event)
        self._worker = self._hass.async_create_background_task(
            self._run(), "dess_monitor_local anomaly log"
        )
        _LOGGER.info("Anomaly log active, writing to %s", self._base)

    @callback
    def async_stop(self) -> None:
        diag_hub.unsubscribe_anomalies(self._on_event)
        if self._worker:
            self._worker.cancel()
            self._worker = None
        if self._dropped:
            _LOGGER.warning(
                "anomaly_log: dropped %d events (writer could not keep up)",
                self._dropped,
            )

    async def async_purge(self) -> int:
        return await self._hass.async_add_executor_job(
            purge, self._base, RETENTION_DAYS
        )
