# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Home Assistant integration for one air conditioner: the GREE Soyal Inverter GWH09AKCXD-K6DNA1A,
controlled over the local network via UDP port 7000. Domain: `gree`.

This is a fork of RobHofmann/HomeAssistant-GreeClimateComponent narrowed to that single model.
Support for hardware and setups this unit does not have was deleted rather than kept behind feature
checks — encryption v1, YAML configuration, cross-VLAN unicast discovery, sub-devices, the `uid`
parameter, the outside temperature and room humidity sensors and the anti-direct-blow switch are all
gone. Keep it that way: when a change would only matter to some other Gree model, it belongs
upstream, not here.

**Dependencies**: `pycryptodome` (AES-GCM), `aiofiles`

## Development Notes

- **No build system, test suite, or linting configuration exists.**
- All code lives under `custom_components/gree/`.
- To test, copy `custom_components/gree/` into a Home Assistant `custom_components/` directory and
  **restart Home Assistant fully**. Reloading the integration re-runs setup but keeps the
  already-imported Python modules, so edits to `const.py` and friends will not take effect.
- Claims about what the unit supports should be checked against the unit, not against a manual.
  The manual for the neighbouring GWH09AKC variant states this series has no Health function; the
  unit answers the `Health` column and the button works.

## Architecture

### Data Flow

```
HA UI action → Entity method → GreeClimate.SyncState({...}) → SendStateToAc()
    → AES-GCM encrypt → UDP packet to device:7000 → device response
    → decrypt → update _acOptions dict → update HA entity state

Polling: every 60s via async_update() → GreeGetValues()
```

### Key Files

| File | Purpose |
|---|---|
| `__init__.py` | Config entry setup, one-shot feature probe, platform forwarding (climate/switch/number/select) |
| `climate.py` | Core file. `GreeClimate(ClimateEntity)` — HVAC control, state polling, temperature handling, all AC commands |
| `gree_protocol.py` | UDP communication, AES-GCM, device discovery, key negotiation, retry logic (8 attempts with backoff) |
| `config_flow.py` | UI config flow: discovery → naming → setup. Also the options flow |
| `const.py` | Protocol constants and the mode mappings (Gree protocol values ↔ HA values) |
| `helpers.py` | Temperature math: 0.5°C precision (SetTem/TemRec), °F↔°C, ±40°C sensor offset auto-detection |
| `entity.py` | `GreeEntity` base class, `GreeEntityDescription` dataclass |
| `switch.py` | Toggle entities (x-fan, lights, health, auxiliary heat, sleep, power save, …) |
| `number.py` | Target temperature step |
| `select.py` | i Sense airflow mode and external temperature sensor selection |

### Feature Detection

`OPTIONAL_FEATURES` in `climate.py` lists the status columns that only some units answer.
`DetectOptionalFeatures()` probes each one and caches the result, `__init__.py` runs it once before
the platforms are set up, and the platforms skip descriptions whose `exists_fn` returns false.

A unit without the hardware answers a status request for that column with an **empty result** — that
is the test. Do not test for a falsy value: `SmartWind` reads `0` when i Sense is off, and treating
that as missing hardware is exactly the bug this replaced. Room humidity is the documented exception
(`zero_means_absent`), because units without the sensor do answer with a constant `0`.

If the unit is unreachable at setup, detection stays `None` and every entity is created.

### Device State

`GreeClimate._acOptions` tracks: `Pow`, `Mod`, `SetTem`, `WdSpd`, `Blo`, `Health`, `SwhSlp`, `Lig`,
`SwingLfRig`, `SwUpDn`, `Quiet`, `Tur`, `StHt`, `TemUn`, `HeatCoolType`, `TemRec`, `SvSt`, `SlpMod`,
`AssHt`, plus `TemSen`, `LigSen` and `SmartWind` once detected.

`SmartWind` is i Sense: `0` off, `1` smart, `2` follow, `3` avoid, `4` surround. It only works while
cooling or heating and steers both louvers by itself.

Louver positions are in `MODES_MAPPING`. The vertical louver stops at `0`–`6`; the horizontal one
adds `12` (flaps apart) and `13` (sweep across the middle region). The values in between that
upstream lists for the vertical louver do not exist on this unit.

Three things are not reachable over the protocol at all: Breeze (no status column carries it),
Auto clean (`AutoClean` stays `0` while the cycle runs) and the beeper (no `Buzzer_ON_OFF` or
`BuzzerCtrl` column; a command carrying only those gets no reply).

Every remaining switch and climate option was verified by writing its current value back and
checking the unit echoes the option — that is how the dead beeper switch was found.

### Temperature Handling

The AC uses integer `SetTem` plus a `TemRec` bit for 0.5°C precision. Some devices report sensor
temps with a +40°C offset; `TempOffsetResolver` auto-detects which mode the device uses from
observed history. Fahrenheit support uses custom conversion functions, not simple formulas, because
of protocol quirks.

### Configuration

UI config flow only, with auto-discovery. The options flow allows runtime changes to the available
modes and the sensor offset; saving reloads the entry.
