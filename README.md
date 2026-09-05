# DESS Monitor Local — Anern ECO-6200 fork

A fork of [Antoxa1081/home-assistant-dess-monitor-local](https://github.com/Antoxa1081/home-assistant-dess-monitor-local),
adapted for an **Anern ECO-6200 inverter behind an EyBond Wi-Fi Plug Pro-05 dongle**.

All the hard work — the EyBond reverse-TCP transport, the protocol decoders, the hub and
coordinator design — is Antoxa1081's. This fork only changes what that inverter needs.
It keeps the `dess_monitor_local` domain, so it is a drop-in replacement: entity IDs do not
change.

The changes are hardware-specific and would not be right for every inverter the upstream
project supports, which is why they live here rather than in a pull request.

The debug panel comes from upstream's `v1.1.0-beta.2` pre-release. Where each release starts, and
what it changed, is in [CHANGELOG.md](CHANGELOG.md).

## What is different

Full detail, per release, with the field measurements: **[CHANGELOG.md](CHANGELOG.md)**.

| Area | What this fork does | Since |
|---|---|---|
| Charger priority | *Solar priority* is reachable locally, over Modbus register `0x1399` through the dongle, because PI30 `PCP00` silently does nothing on this inverter | `anern.1` |
| Select names | Options carry the inverter's own names instead of the generic PI30 ones, which meant the opposite (mapping [below](#after-installing-or-upgrading)) | `anern.1` |
| Select read-back | A change made from Home Assistant shows within one cycle instead of up to 12 minutes | `anern.1` |
| NAK handling | A NAK is a failed read, not a successful one, so it no longer blanks a section for a cycle | `anern.1` |
| Truncated frames | A short `QPIGS` is rejected rather than decoded field by field into `unknown`s | `anern.3`, widened in `anern.4` |
| vSoC estimator | Its input entities are resolved from the entity registry by `unique_id`, not guessed from the device name | `anern.5` |
| Frame integrity | Strict CRC validation on **every** command, on by default, wired on the EyBond transport | `anern.6` |
| Diagnostics | Failed reads are recorded instead of vanishing: JSONL on disk, captured frame bytes, seven counters as entities, an Anomalies view in the debug panel | `anern.6` |
| Freshness | `stale` and `stale_cycles` on every typed sensor, because a frozen section otherwise looks perfectly fresh from outside | `anern.6` |

## Installation

Add this repository as a HACS custom repository (category: Integration), then install and restart
Home Assistant. Replacing the upstream integration keeps every entity ID, because the domain is
unchanged.

Setup and configuration are otherwise identical to upstream — see
[its README](https://github.com/Antoxa1081/home-assistant-dess-monitor-local) and wiki.

## After installing or upgrading

- ⚠️ **Turn on strict CRC validation once**, from **Configure → Connection**. It is the default for
  a new entry, but an existing config entry keeps its stored value on upgrade.
- **The anomaly log is on by default.** It writes `anomalies-YYYY-MM-DD.jsonl` to
  `config/dess_monitor_local/`, plus a `frames-<CMD>-<timestamp>.json` dump for the anomalies whose
  cause is in the bytes. Files older than 30 days are removed. The option is under
  **Configure → Anomaly log**.
- `truncated_frames` normally stays at 0 once CRC validation is on: a short frame is rejected
  before the decoder counts its fields. It is not a sign that truncation stopped.

⚠️ **Select options were renamed in `anern.1`**, because the generic PI30 names describe a
different device: on this inverter register 0 is *Solar priority*, not "utility first", so reading
the old select told you the opposite of what the inverter was doing.

The new names are not ours. They are the ones the vendor's own tools use for this inverter: the
**SmartESS** app, the **[dessmonitor.com](https://www.dessmonitor.com)** portal and the cloud API
behind both. Anyone comparing Home Assistant with the app now reads the same word in both places.

The old names are still accepted as aliases, so existing automations keep working, but new ones
should use these:

| register | upstream option | this fork, and the vendor cloud |
|---|---|---|
| 0 | `UtilityFirst` | `Solar priority` |
| 1 | `SolarFirst` | `Solar and mains` |
| 2 | `SolarAndUtility` | `Solar only` |

Output priority changes one name for the same reason, `UtilityFirst` to `Utility`.

## Debug panel

Enabled from the integration options; it appears in the sidebar as **DESS Debug**.

**Live** streams protocol events while the panel is open. **Anomalies** reads the history from
disk and groups events into incidents — one dropped session fails every command queued behind it —
and captured frames can be opened inline, with the terminating byte flagged.

The panel also takes a `RAW` command, which sends bytes straight through the EyBond envelope,
bypassing the protocol adapters. It is the most useful diagnostic tool on this hardware, and it is
how the Modbus register above was found:

```
RAW 1 0x994 05 06 13 99 00 02 dd 24
```

## Hardware this is tested on

- Anern ECO-6200 / SCI-EVO-6200, 6200 W, 48 V
- EyBond Wi-Fi Plug Pro-05 dongle, reverse-TCP on port 8899
- 48 V 100 Ah LiFePO4 bank, 1720 Wp of panels

Nothing here has been tested on any other combination.
