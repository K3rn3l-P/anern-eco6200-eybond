"""The anomaly channel: separate from the hot path, and persistent.

Two properties matter and neither is obvious from the call sites.

First, attaching a persistent sink must NOT switch on the per-frame
instrumentation. The transport guards its hot-path events with
diag_hub.active(); if a sink flipped that true, every frame of every poll
would start building event dicts — the "zero cost in normal operation" the
module promises would quietly disappear.

Second, the retention sweep must only delete what this module wrote.
"""
from __future__ import annotations

import json
import os
import time

import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local import diag_hub  # noqa: E402
from custom_components.dess_monitor_local.anomaly_log import (  # noqa: E402
    purge,
    write_event,
)


@pytest.fixture(autouse=True)
def clean_hub():
    diag_hub.clear()
    yield
    diag_hub.clear()


class TestChannelSeparation:
    def test_sink_does_not_arm_the_hot_path(self):
        diag_hub.subscribe_anomalies(lambda e: None)
        assert diag_hub.active() is False
        assert diag_hub.anomaly_active() is True

    def test_hot_path_publish_still_noop_with_only_a_sink(self):
        seen = []
        diag_hub.subscribe_anomalies(seen.append)
        diag_hub.publish({"t": "frame", "cmd": "QPIGS"})
        assert seen == []
        assert diag_hub.recent() == []

    def test_anomaly_reaches_the_sink_with_no_panel(self):
        seen = []
        diag_hub.subscribe_anomalies(seen.append)
        diag_hub.publish_anomaly({"kind": "freeze", "cmd": "QMOD"})
        assert len(seen) == 1
        assert seen[0]["kind"] == "freeze"
        assert seen[0]["t"] == "anomaly"
        assert "ts" in seen[0]

    def test_anomaly_is_ringed_even_with_nobody_listening(self):
        # So a panel opened after the fact still sees what just happened.
        diag_hub.publish_anomaly({"kind": "rejected", "cmd": "QPIGS"})
        assert [e["kind"] for e in diag_hub.recent()] == ["rejected"]

    def test_unsubscribe_detaches(self):
        seen = []

        def sink(event):
            seen.append(event)

        diag_hub.subscribe_anomalies(sink)
        diag_hub.unsubscribe_anomalies(sink)
        diag_hub.publish_anomaly({"kind": "freeze"})
        assert seen == []
        assert diag_hub.anomaly_active() is False


class TestSinkFailure:
    def test_a_raising_sink_does_not_break_the_caller(self):
        # This runs on the polling path. An unwritable disk is not a reason to
        # stop polling the inverter.
        def boom(_event):
            raise RuntimeError("disk on fire")

        good = []
        diag_hub.subscribe_anomalies(boom)
        diag_hub.subscribe_anomalies(good.append)
        diag_hub.publish_anomaly({"kind": "freeze"})
        assert len(good) == 1


class TestPersistence:
    def test_write_event_appends_jsonl(self, tmp_path):
        write_event(tmp_path, {"kind": "freeze", "cmd": "QMOD", "ts": time.time()})
        write_event(tmp_path, {"kind": "rejected", "cmd": "QPIGS", "ts": time.time()})
        files = list(tmp_path.glob("anomalies-*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").strip().splitlines()
        assert [json.loads(l)["kind"] for l in lines] == ["freeze", "rejected"]


class TestPurge:
    def _aged(self, path, days):
        old = time.time() - days * 86400
        path.write_text("x", encoding="utf-8")
        os.utime(path, (old, old))

    def test_removes_only_old_own_files(self, tmp_path):
        self._aged(tmp_path / "anomalies-2020-01-01.jsonl", 40)
        self._aged(tmp_path / "frames-QPIGS-20200101-000000.json", 40)
        fresh = tmp_path / "anomalies-2030-01-01.jsonl"
        fresh.write_text("x", encoding="utf-8")

        assert purge(tmp_path, 30) == 2
        assert fresh.exists()

    def test_leaves_foreign_files_alone(self, tmp_path):
        # A glob of "*" would take these too, silently, after 30 days.
        readme = tmp_path / "README.md"
        notes = tmp_path / "notes.txt"
        self._aged(readme, 400)
        self._aged(notes, 400)

        assert purge(tmp_path, 30) == 0
        assert readme.exists()
        assert notes.exists()
