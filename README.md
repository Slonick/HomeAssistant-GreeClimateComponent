# Gree Soyal climate component for Home Assistant

Home Assistant integration for the **GREE Soyal Inverter GWH09AKCXD-K6DNA1A**, controlled directly
over the local network.

This is a fork of [RobHofmann/HomeAssistant-GreeClimateComponent](https://github.com/RobHofmann/HomeAssistant-GreeClimateComponent),
narrowed to a single model. The upstream project supports a wide range of Gree units and carries the
code needed for all of them; everything here is verified against one unit, and whatever that unit
does not have was removed rather than kept behind a feature check. If you own a different Gree
model, use upstream instead.

The integration talks to the unit at its IP address on the local network. The official app only
connects directly during initial setup and then works through Gree's servers.

## What this fork changes

### Added

- **i Sense** (`select`) — the body sensing device aims the airflow relative to the people in the
  room: `off`, `smart`, `follow`, `avoid`, `surround`. Only available while cooling or heating,
  and it drives both louvers by itself. Maps to the `SmartWind` status column.
- **Auxiliary heat** (`switch`) — the E-HEATER button, available in heating mode. Maps to `AssHt`.
- **Ukrainian translation.**

### Fixed

- **Louver positions.** The horizontal louver was missing two of its positions: the two flaps
  pointing apart (`12`) and a sweep across the middle region (`13`). The vertical louver never had
  the swing-in-a-region positions upstream lists (`7`–`11`); the unit does not offer them.
- **Feature detection.** A feature was treated as missing whenever its status column read a falsy
  value, so anything legitimately reading `0` looked absent — `SmartWind` among them, which is `0`
  when i Sense is off. A unit without the hardware answers with an empty result instead, so that is
  what is tested now. Room humidity keeps the old rule, because units without the sensor do answer
  the column with a constant `0`.
- **Discovery.** Replies were only decrypted with the v1 ECB key, so a unit that speaks GCM — this
  one does — was silently skipped.
- **Entity creation.** Detection now runs once before the platforms are set up and the platforms
  honour `exists_fn`, so entities are no longer created for hardware the unit does not have. When
  the unit is unreachable at setup, detection stays undecided and every entity is created, as before.

### Removed

Not present on this model, confirmed against the unit and the owner's manual:

- Anti direct blow, outside temperature sensor, room humidity sensor — the unit answers those
  status columns with an empty result. The whole `sensor` platform went with them.
- Fresh air (`Air`) — no such control on the remote or in the app.

Not reachable over the protocol:

- **Breeze.** The remote has the button and the manual documents the function, but no status column
  carries it; roughly 160 candidate names were probed. It cannot be controlled from Home Assistant.
- **Auto clean.** Runs from the remote (MODE + FAN held for 5 seconds, `CL` on the display). The
  `AutoClean` column stays `0` throughout the cycle, so there is nothing to expose.
- **Beeper.** The unit has no `Buzzer_ON_OFF` or `BuzzerCtrl` column and drops a command that
  carries only those, so the switch upstream offers could not affect anything here.

Generic support that this model does not need:

- Encryption v1 (ECB). This unit speaks GCM only, so the version detection, the key-binding path
  and the config option are gone.
- YAML configuration — the integration is set up through the UI.
- Cross-VLAN unicast discovery, sub-device (multi-split) support, and the `uid` parameter.

Sleep is still a plain on/off switch. The remote offers four sleep curves, but Home Assistant
schedules temperature better than the unit does.

## Entities

| Entity | Notes |
|---|---|
| `climate` | Modes, target temperature, fan speed, both louvers |
| `select` i Sense | Cooling and heating only |
| `select` external temperature sensor | Use another sensor instead of the built-in one |
| `switch` X-Fan | Dries the indoor unit after shutdown |
| `switch` Health | Ionisation |
| `switch` Auxiliary heat | Heating only |
| `switch` Power save | Cooling only |
| `switch` 8 °C heat | Heating only |
| `switch` Sleep | Cooling and heating only |
| `switch` Lights, Light sensor | Display backlight and its automatic brightness |
| `switch` Auto X-Fan, Auto Light | Integration-side automation, not unit state |
| `number` Temperature step | Step used by the up/down controls |

Fan speeds are `auto`, five levels, `quiet` and `turbo`, matching the remote.

## Installation

Through HACS as a custom repository:

1. HACS → three-dot menu → **Custom repositories**
2. Repository `https://github.com/Slonick/HomeAssistant-GreeClimateComponent`, type **Integration**
3. Download it, then restart Home Assistant

Editing files in `custom_components/gree` in place needs a **full restart**, not an integration
reload — reloading re-runs the setup but keeps the already-imported Python modules.

## Setup

**Settings → Devices & Services → Add Integration → Gree**, then either discovery or manual entry
with the IP address, port `7000` and the MAC address. Leave the encryption key empty; the
integration binds to the unit and fetches it.

## Logging

```yaml
logger:
  default: error
  logs:
    custom_components.gree: debug
```

## Credits

- [RobHofmann/HomeAssistant-GreeClimateComponent](https://github.com/RobHofmann/HomeAssistant-GreeClimateComponent) — the upstream integration
- [gree-remote](https://github.com/tomikaa87/gree-remote) — the protocol
