# Changelog

Releases of this fork, newest first. Versions are tagged `v1.1.0-anern.N` and carry the same
number in `manifest.json`, which is what HACS compares.

Each entry records what changed, why it was needed on this hardware, and — where there is one —
the evidence from the live plant. Measurements are dated because they are observations, not
estimates.

Everything before `anern.1` is upstream history and is not listed here.

---

## v1.1.0-anern.6 — 2026-09-03

Diagnostics. Before this release three quarters of all failed reads left no trace at all, and the
only evidence of a corrupt frame was a DEBUG line in a rolling log window.

### CRC validation on every command

The decoders only check length where a section has a known field count, which left `QMOD`,
`QPIGS2`, `QPIRI` and `QFWS` unprotected — and `QMOD` is what a controller reads to decide the
operating regime. A CRC covers all of them without knowing anything about their shape, so strict
CRC validation is now **on by default** and is wired on the EyBond transport, where the option
previously existed but did nothing.

The check lives in the adapter, not the transport: `send_eybond_bytes` is shared with Modbus (a
different CRC — it is the path that writes the charger priority), with set commands and with PI18,
so validating there would reject every Modbus reply.

A `QPIWS` frame carrying fewer than 32 status bits is rejected too, instead of being padded with
`False`. Padding is not a degraded reading of an alarm; it is the assertion that the alarm is
absent, from a frame that never said so, on a `binary_sensor` that stays reassuringly `off`.

**Bench evidence** — 151 frames captured from the live plant, 1–3 Sep 2026: 142/142 good frames
accepted, 10/10 truncated frames rejected, no false positives either way. Every truncated frame
*in that capture* ends in a high byte (`0xff`/`0xfe`/`0xf3`/`0xf0`) where the CR should be, and
cuts at an arbitrary offset — a framing error on the inverter-to-dongle serial leg, not a parsing
bug.

**Field evidence, since the release.** The check has rejected frames on `QPIWS` and `QPIGS2`
(3 Sep), `QPIRI` (4 Sep) and `QMOD` (**5 Sep 2026, 14:27**), each time with a successful retry and
no sensor left stale. The `QMOD` frame is worth recording: it was a **single `0x28` byte** — a bare
`(` — where a good reply is the five bytes `28 42 e7 c9 0d`. It does not match the high-byte
signature above, and no field-count check could have caught it, because the `qmod` decoder takes
`[:1]` and never calls `zip()`. On this command the CRC is the only possible defence.

⚠️ An existing config entry keeps its stored value, so after upgrading, the option has to be
switched on once from **Configure → Connection**.

### Failed reads are recorded instead of vanishing

The transport returns `None` on a write failure, a reply timeout or a lost session; the adapter
turned that into an empty dict, and the coordinator dropped it without logging anything. Two days
of capture: 88 freezes imply at least 176 failed attempts, of which 45 were logged and 0 were
exceptions.

Rejections, freezes, no-response cycles and sections going dark now emit a structured event. With
**Anomaly log** enabled (the default) they are written to `config/dess_monitor_local/` as JSONL,
together with a dump of the raw frame ring for the kinds whose cause is in the bytes — the ring
lives in RAM and scrolls in minutes, so it is captured at the moment of the anomaly.

Anomalies travel on a channel separate from the panel's live stream, so recording them does not
switch on the per-frame instrumentation: the hot path stays free when no panel is open.

### Anomaly counters as entities

Seven counters plus a "last event" sensor, one per kind: NAKs, truncated frames, CRC failures,
no-response, transport errors, freezes and sections gone dark. Each carries a `per_command`
breakdown, and they are cumulative across restarts — a counter that resets on reboot cannot answer
the only question it exists for. Being entities they are recorded, so they graph and can drive an
automation, which the JSONL cannot. The rare kinds also fire a `dess_monitor_local_anomaly` event
on the Home Assistant bus.

⚠️ With strict CRC on, `truncated_frames` will usually stay at 0 by construction, not because
truncation stopped: the CRC intercepts a short frame before the decoder counts its fields.

### An Anomalies window in the debug panel

Two exclusive views. **Live** is the existing stream, unchanged. **Anomalies** reads the history
from disk and shows incidents rather than raw events — one dropped session fails every command
queued behind it, so 142 events were 97 actual incidents on the real capture. Captured frames can
be opened inline, with the terminating byte flagged.

### Sensors report whether their section is frozen

`stale` and `stale_cycles` on every typed sensor. On a failed read this integration returns the
last known value, so the entity is rewritten and `last_reported` moves forward: from the outside a
frozen section looks perfectly fresh, and a consumer measuring staleness from timestamps cannot
tell the difference.

---

## v1.1.0-anern.5 — 2026-09-01

**The vSoC estimator resolves its inputs by unique_id, not by name.** This is the fix for the
whole vSoC family sitting at `unavailable`/`unknown` while its settings are correctly filled in,
with nothing in the log.

`DirectBatteryStateOfChargeSensor` built the entity_ids of its six inputs — capacity, sync
voltage, battery mode, and the three float-deadband controls — by slugifying the device name. That
guess is only right when Home Assistant happens to assign the object_id the name suggests. On the
install where this was found every entity carried an extra prefix, so all six guesses missed: the
estimator watched entity_ids that did not exist, never saw the capacity, and reported
`unavailable` forever. Writing the capacity again changed nothing, and neither did restarting.

The ids now come from the **entity registry, by unique_id**, which this integration generates
itself, so the answer is definitive. The old guess remains only as a fallback for the moment
before an entity is registered.

Three smaller things came with it:

- the listeners moved out of `__init__`, where the registry is not usable yet, into
  `async_added_to_hass`;
- they are re-attached whenever the registry changes, so renaming an entity no longer switches the
  estimator off silently;
- the capacity is read once at startup instead of only being watched for changes — a value already
  set was invisible until the user touched it again.

⚠️ `discharge_floor` has a unique_id ending in `discharge_floor_soc` and an entity_id ending in
`vsoc_discharge_floor`. They are not interchangeable.

**Field evidence** — 2026-09-01. Before: `battery_state_of_charge` and the four derived sensors
all `unavailable`/`unknown` with capacity set to 100 Ah. After: SoC live and the runtime estimates
populated (`backup_time_at_current_load` 14.7 h, `time_to_discharge_floor` 12.0 h), with the
entity_ids left prefixed — which is the point.

Note that the estimator starts its Coulomb counter at 100%, so the first reading after enabling it
is as wrong as the pack is empty; it re-anchors on the first snap-to-100.

---

## v1.1.0-anern.4 — 2026-08-31

**The truncated-`QPIGS` check widened past `battery_voltage` alone.** The first version of the
check only noticed a frame short enough to lose the battery voltage. Every field through
`device_status_bits_b10_b8` (index 20) is read by some sensor — `direct_sensor.py` uses everything
through `pv_charging_power` (19), `binary_sensor.py` additionally reads the fault/warning bits at
20 — so the threshold is now 21 fields. Nothing reads `reserved_a`/`reserved_bb`/`reserved_cccc`
(21–23), and a frame missing only those is still complete for every consumer that exists.

**Field evidence** — the first real truncation arrived on 2026-09-01 at 12:25:02:
`QPIGS frame too short: 15 of 24 fields (need 21)`. The retry succeeded, so no freeze and no
`unknown`.

Also in this release: the NAK fix of `anern.1` marked as confirmed rather than under observation
(see below).

---

## v1.1.0-anern.3 — 2026-08-31

**A truncated `QPIGS` frame is rejected instead of being decoded partially.** The EyBond transport
is not CRC-checked at this point in the history, so a mangled frame with a stray `\r` mid-payload
is cut short by `response.partition(b"\r")` before it reaches the decoder. Fewer tokens than
fields is silent with `zip()`, and such a frame is neither empty nor a NAK, so it read as a
successful — if incomplete — response, and each dropped field went `unknown` on its own sensor,
one at a time as the truncation varied cycle to cycle.

---

## v1.1.0-anern.2 — 2026-08-31

Italian translations for the config and options flows.

---

## v1.1.0-anern.1 — 2026-08-30

The release that adapts the integration to an Anern ECO-6200 behind an EyBond Wi-Fi Plug Pro-05.

### Solar priority can be set locally

The charger source priority on this inverter has three modes, and the PI30 `PCP` command only
reaches two of them: `PCP00` answers `(ACK` and leaves the register untouched, so *Solar priority*
was unreachable from the local channel.

The vendor cloud does not use PI30 for this setting — it writes **Modbus holding register 0x1399
(5017)** through the same dongle, on Modbus slave id 5. `EyBondAdapter` now does the same, and all
three modes work. The dongle turned out to be a plain serial passthrough: neither the envelope
`devaddr` nor the `devcode` matter, the bytes reach the serial line either way.

### The selects use the inverter's own names

The generic PI30 names describe a different device. On this inverter register 0 is *Solar
priority*, not "utility first" — that is what the app, the web portal and the cloud API all call
it. Reading the old select told you the opposite of what the inverter was doing.

| register | upstream option | this fork |
|---|---|---|
| 0 | `UtilityFirst` | `Solar priority` |
| 1 | `SolarFirst` | `Solar and mains` |
| 2 | `SolarAndUtility` | `Solar only` |

Output priority changes one name, `UtilityFirst` to `Utility`, matching the cloud select.

⚠️ **Breaking change for existing automations.** The old names are still accepted as aliases,
translated in `async_handle_select_option` — before Home Assistant validates the option, because
HA raises `not_valid_option` before `async_select_option` ever runs. An automation still passing
an old name keeps working instead of failing silently.

### Selects reflect a change within one cycle

Both priorities live in `QPIRI`, which is polled every 12 cycles as essentially static data, and
the select never wrote its optimistic value to the state machine. A change took up to 12 minutes
to show (11 minutes, measured).

Two fixes: `async_write_ha_state()` after the optimistic write, and
`DirectCoordinator.force_section()`, which polls one section on the next cycle regardless of its
cadence. Measured after the change: 73 and 74 seconds. No extra traffic at rest — the 12-cycle
cadence still governs routine polling.

This covers writes made through Home Assistant. A change made from the app, the portal or the
inverter's own panel is still subject to the normal cadence.

### A NAK is no longer counted as a successful read

This is the fix for sensors intermittently going `unavailable`/`unknown` for one cycle.

`fetch_with_retry` treated any truthy result as success, including the error dict a NAK produces.
A NAK is a well-formed frame carrying no data, so the freeze in `FailureTracker` never engaged and
the section was blanked for a whole cycle. Commands the firmware NAKs by design (`QPIGS2`, `QFWS`)
remain exempt.

The NAK itself is the inverter's behaviour, not this integration's: the vendor cloud gets NAKed on
`QPIGS` too, roughly two requests in five. This fix stops a NAK from wiping the last known values;
it does not stop the inverter from NAKing.

**Field evidence** — confirmed in production on 2026-08-31, watched from 11:25 to 15:50 at peak PV
(974 W). Over that window: 28 `QPIGS` cycles sent, 25 valid responses, 3 lost to an empty reply, a
NAK and a `Connection reset by peer` — and **zero sensors went `unknown`**. The freeze engaged
twice in the afternoon (14:56:59 and 15:44:23) after both attempts of a cycle failed, holding the
last good values with nothing visible from outside. Seven rejections were logged in total, five on
`QPIGS` and two on `QPIWS`. The critical failure case — a *valid* frame being discarded — never
occurred.

The upstream issue tracker has several reports that look like this one, including on other Anern
units.

### Also in this release

- A `RAW` command in the debug panel: `RAW <devaddr> <devcode> <hex bytes…>` sends bytes straight
  through the EyBond envelope, bypassing the protocol adapters. It is how the Modbus register above
  was found.
- A pull request template pointing at the right base repository — GitHub defaults a fork's PR base
  to upstream.
