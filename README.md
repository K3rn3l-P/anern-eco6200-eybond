# DESS Monitor Local — Anern ECO-6200 fork

A fork of [Antoxa1081/home-assistant-dess-monitor-local](https://github.com/Antoxa1081/home-assistant-dess-monitor-local),
adapted for an **Anern ECO-6200 inverter behind an EyBond Wi-Fi Plug Pro-05 dongle**.

All the hard work — the EyBond reverse-TCP transport, the protocol decoders, the hub and
coordinator design — is Antoxa1081's. This fork only changes what that inverter needs.
It keeps the `dess_monitor_local` domain, so it is a drop-in replacement: entity IDs do not
change.

The changes are hardware-specific and would not be right for every inverter the upstream
project supports, which is why they live here rather than in a pull request.

## What is different

### Solar priority can be set locally

The charger source priority on this inverter has three modes, and the PI30 `PCP` command
only reaches two of them: `PCP00` answers `(ACK` and leaves the register untouched, so
*Solar priority* was unreachable from the local channel.

The vendor cloud does not use PI30 for this setting — it writes **Modbus holding register
0x1399 (5017)** through the same dongle, on Modbus slave id 5. `EyBondAdapter` now does the
same, and all three modes work.

The dongle turned out to be a plain serial passthrough: neither the envelope `devaddr` nor
the `devcode` matter, the bytes reach the serial line either way.

### The selects use the inverter's own names

The generic PI30 names describe a different device. On this inverter register 0 is
*Solar priority*, not "utility first" — that is what the app, the web portal and the cloud
API all call it. Reading the old select told you the opposite of what the inverter was doing.

| register | upstream option | this fork |
|---|---|---|
| 0 | `UtilityFirst` | `Solar priority` |
| 1 | `SolarFirst` | `Solar and mains` |
| 2 | `SolarAndUtility` | `Solar only` |

Output priority changes one name, `UtilityFirst` to `Utility`, matching the cloud select.

⚠️ **This is a breaking change for existing automations.** The old names are still accepted
as aliases, translated in `async_handle_select_option` — before Home Assistant validates the
option, because HA raises `not_valid_option` before `async_select_option` ever runs. An
automation still passing an old name keeps working instead of failing silently.

### Selects reflect a change within one cycle

Both priorities live in `QPIRI`, which is polled every 12 cycles as essentially static data,
and the select never wrote its optimistic value to the state machine. A change took up to
12 minutes to show (11 minutes, measured).

Two fixes: `async_write_ha_state()` after the optimistic write, and
`DirectCoordinator.force_section()`, which polls one section on the next cycle regardless of
its cadence. Measured after the change: 73 and 74 seconds. No extra traffic at rest — the
12-cycle cadence still governs routine polling.

This only covers writes made through Home Assistant. A change made from the app, the portal
or the inverter's own panel is still subject to the normal cadence.

### A NAK is no longer counted as a successful read

This is the fix for sensors intermittently going `unavailable` / `unknown` for one cycle.

`fetch_with_retry` treated any truthy result as success, including the error dict a NAK
produces. A NAK is a well-formed frame carrying no data, so the freeze in `FailureTracker`
never engaged and the section was blanked for a whole cycle. Commands the firmware NAKs by
design (`QPIGS2`, `QFWS`) are still exempt.

The NAK itself is the inverter's behaviour, not this integration's: the vendor cloud gets
NAKed on `QPIGS` too, roughly two requests in five. This fix stops a NAK from wiping the
last known values; it does not stop the inverter from NAKing.

✅ **Confirmed in production** on 2026-08-31, watched from 11:25 to 15:50 at peak PV (974 W).
Over that window: 28 `QPIGS` cycles sent, 25 valid responses, 3 lost to an empty reply, a
NAK and a `Connection reset by peer` — and **zero sensors went `unknown`**. The freeze
engaged twice in the afternoon (14:56:59 and 15:44:23) after both attempts of a cycle
failed, holding the last good values with nothing visible from outside. Seven rejections
were logged in total, five on `QPIGS` and two on `QPIWS`.

The critical failure case — a *valid* frame being discarded — never occurred.

The upstream issue tracker has several reports that look like this one, including on other
Anern units.

### The vSoC estimator finds its settings by unique_id, not by name

This is the fix for the whole vSoC family sitting at `unavailable` / `unknown` while its
settings are correctly filled in, with nothing in the log.

`DirectBatteryStateOfChargeSensor` built the entity_ids of its six inputs — capacity, sync
voltage, battery mode, and the three float-deadband controls — by slugifying the device name.
That guess is only right when HA happens to assign the object_id the name suggests. On the
install where this was found every entity carried an extra prefix, so **all six guesses
missed**: the estimator watched entity_ids that did not exist, never saw the capacity, and
reported `unavailable` forever. Writing the capacity again changed nothing, and neither did
restarting.

The ids are now resolved from the **entity registry by unique_id**, which this integration
generates itself, so the answer is definitive. The old guess is kept only as a fallback for
the moment before an entity is registered.

Three smaller things came with it:

- the listeners moved out of `__init__`, where the registry is not usable yet, into
  `async_added_to_hass`;
- they are re-attached whenever the registry changes, so **renaming an entity no longer
  switches the estimator off** silently;
- the capacity is read once at startup instead of only being watched for changes — a value
  already set was invisible until the user touched it again.

⚠️ Careful if you touch this: `discharge_floor` has a unique_id ending in
`discharge_floor_soc` and an entity_id ending in `vsoc_discharge_floor`. They are not
interchangeable.

✅ **Confirmed in production** on 2026-09-01. Before: `battery_state_of_charge` and the four
derived sensors all `unavailable`/`unknown` with capacity set to 100 Ah. After: SoC live and
the runtime estimates populated (`backup_time_at_current_load` 14.7 h,
`time_to_discharge_floor` 12.0 h), with the entity_ids left prefixed — which is the point.

Note the estimator starts its Coulomb counter at 100%, so the first reading after enabling it
is as wrong as the pack is empty; it re-anchors on the first snap-to-100.

### Corrupt frames are rejected, on every command

The decoders only check length where a section has a known field count, which left `QMOD`,
`QPIGS2`, `QPIRI` and `QFWS` unprotected — and `QMOD` is what a controller reads to decide the
operating regime. A CRC covers all of them without knowing anything about their shape, so
**Strict CRC validation is now on by default** and is finally wired on the EyBond transport,
where the option previously existed but did nothing at all.

The check lives in the adapter, not the transport: `send_eybond_bytes` is shared with Modbus (a
different CRC — it is the path that writes the charger priority), with set commands and with
PI18, so validating there would reject every Modbus reply.

✅ **Measured on 151 frames captured from a live plant** (1–3 Sep 2026): 142/142 good frames
accepted, 10/10 truncated frames rejected, no false positives either way. Every truncated frame
ends in a high byte (`0xff`/`0xfe`/`0xf3`/`0xf0`) where the CR should be and cuts at an arbitrary
offset — a framing error on the inverter-to-dongle serial leg, not a parsing bug.

⚠️ An existing config entry keeps its stored value, so on an upgrade the option has to be switched
on once from **Configure → Connection**.

A `QPIWS` frame carrying fewer than 32 status bits is rejected too, instead of being padded with
`False`. Padding is not a degraded reading of an alarm — it is the assertion that the alarm is
absent, from a frame that never said so, on a `binary_sensor` that stays reassuringly `off`.
Caught its first real case on 2 Sep 2026: `4 of 32 status bits`.

### Failures are recorded instead of vanishing

Three quarters of all failed reads used to leave no trace. The transport returns `None` on a write
failure, a reply timeout or a lost session; the adapter turned that into an empty dict, and the
coordinator dropped it without logging anything. Two days of capture: 88 freezes imply at least
176 failed attempts, of which 45 were logged and 0 were exceptions.

Those now emit a structured event, and so do rejections, freezes and unavailability. With **Anomaly
log** enabled (default) they are written to `config/dess_monitor_local/` as JSONL, together with a
dump of the raw frame ring for the ones whose cause is in the bytes — the ring lives in RAM and
scrolls in minutes, so it is captured at the moment of the anomaly.

Anomalies travel on a channel separate from the panel's live stream, so recording them does **not**
switch on the per-frame instrumentation: the hot path stays free when no panel is open.

### Anomaly counters as entities

Seven counters plus a "last event" sensor, one per kind: NAKs, truncated frames, CRC failures,
no-response, transport errors, freezes and sections gone dark. Each carries a `per_command`
breakdown, and they are cumulative across restarts — a counter that resets on reboot cannot answer
the only question it exists for. Being entities they are recorded, so they graph and can drive an
automation, which the JSONL cannot.

The rare kinds also fire a `dess_monitor_local_anomaly` event on the HA bus.

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

### A `RAW` command in the debug panel

`RAW <devaddr> <devcode> <hex bytes…>` sends bytes straight through the EyBond envelope,
bypassing the protocol adapters. It is how the Modbus register above was found, and it is
the most useful diagnostic tool on this hardware.

```
RAW 1 0x994 05 06 13 99 00 02 dd 24
```

## Installation

Add this repository as a HACS custom repository (category: Integration), then install and
restart Home Assistant. Replacing the upstream integration keeps every entity ID, because
the domain is unchanged.

Setup and configuration are otherwise identical to upstream — see
[its README](https://github.com/Antoxa1081/home-assistant-dess-monitor-local) and wiki.

## Hardware this is tested on

- Anern ECO-6200 / SCI-EVO-6200, 6200 W, 48 V
- EyBond Wi-Fi Plug Pro-05 dongle, reverse-TCP on port 8899
- 48 V 100 Ah LiFePO4 bank, 1720 Wp of panels

Nothing here has been tested on any other combination.
