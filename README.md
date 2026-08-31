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
