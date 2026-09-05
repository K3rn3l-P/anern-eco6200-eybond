# DESS Monitor Local — Anern ECO-6200 fork

A fork of [Antoxa1081/home-assistant-dess-monitor-local](https://github.com/Antoxa1081/home-assistant-dess-monitor-local),
adapted for an **Anern ECO-6200 inverter behind an EyBond Wi-Fi Plug Pro-05 dongle**, and hardened
for a plant that runs unattended.

It keeps the `dess_monitor_local` domain, so it is a drop-in replacement for the upstream
integration: entity IDs do not change.

## Relationship to upstream

All the hard work — the EyBond reverse-TCP transport, the protocol decoders, the hub and
coordinator design — is Antoxa1081's. This fork adds what this inverter needs and what an
unattended install needs, and nothing else.

The changes are hardware-specific: renaming the select options to the Anern register meanings, or
writing the charger priority over Modbus, would be wrong for other inverters the upstream project
supports. That is why they live here rather than in a pull request. Anything general enough to help
every inverter is better sent upstream — the pull request template says so too.

## What is different

Full detail, per release, with the field measurements: **[CHANGELOG.md](CHANGELOG.md)**.

| Area | What this fork does | Since |
|---|---|---|
| Charger priority | *Solar priority* is reachable locally, over Modbus register `0x1399` through the dongle, because PI30 `PCP00` silently does nothing on this inverter | `anern.1` |
| Select names | Options carry the inverter's own names (`Solar priority`, `Solar and mains`, `Solar only`, `Utility`) instead of the generic PI30 ones, which meant the opposite | `anern.1` |
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
- ⚠️ **Select options were renamed in `anern.1`.** The old PI30 names are still accepted as
  aliases, so existing automations keep working, but new ones should use the names above.
- `truncated_frames` normally stays at 0 once CRC validation is on: a short frame is rejected
  before the decoder counts its fields. It is not a sign that truncation stopped.

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
