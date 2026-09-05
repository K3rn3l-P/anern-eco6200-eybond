"""Incidents, not raw events.

A dongle that drops its TCP session fails every command queued behind it, so
one transport failure surfaces as three or four anomalies on different
commands, seconds apart. Rendering those as separate faults overstates the
failure rate by nearly half, and sends whoever reads the panel looking for
three problems that are one.
"""
from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.dess_monitor_local.anomaly_log import (  # noqa: E402
    EPISODE_WINDOW_S,
    group_episodes,
    summarise,
)

from real_capture import REAL_CAPTURE  # noqa: E402

EVENTS = [
    {"ts": offset, "kind": kind, "cmd": cmd}
    for offset, kind, cmd in REAL_CAPTURE
]


class TestRealCapture:
    def test_dataset_is_what_we_think_it_is(self):
        assert len(EVENTS) == 142

    def test_events_group_into_incidents(self):
        # 142 anomalies, 97 actual incidents.
        assert len(group_episodes(EVENTS)) == 97

    def test_summary_reports_both_numbers(self):
        s = summarise(EVENTS)
        assert s["events"] == 142
        assert s["episodes"] == 97
        assert s["last_ts"] == EVENTS[-1]["ts"]

    def test_freeze_is_the_commonest_kind(self):
        s = summarise(EVENTS)
        assert max(s["by_kind"], key=s["by_kind"].get) == "freeze"

    def test_multi_command_incidents_exist(self):
        spanning = [
            ep for ep in group_episodes(EVENTS)
            if len({e["cmd"] for e in ep}) > 1
        ]
        assert spanning, "expected incidents touching more than one command"


class TestGrouping:
    def test_empty(self):
        assert group_episodes([]) == []

    def test_single_event_is_one_incident(self):
        assert len(group_episodes([{"ts": 100.0}])) == 1

    def test_gap_larger_than_window_splits(self):
        assert len(group_episodes([{"ts": 0.0}, {"ts": EPISODE_WINDOW_S + 1}])) == 2

    def test_gap_within_window_joins(self):
        assert len(group_episodes([{"ts": 0.0}, {"ts": EPISODE_WINDOW_S - 1}])) == 1

    def test_window_is_measured_from_the_previous_event(self):
        # A slow trickle stays one incident as long as each step is inside the
        # window: it is one ongoing disturbance.
        assert len(group_episodes([{"ts": float(i * 20)} for i in range(5)])) == 1
