# Levoit Vital 200S Air Purifier — Home Assistant Integration

A custom Home Assistant integration for the **Levoit Vital 200S (LAP-V201S-WUS)** air purifier, built independently of the official VeSync integration. Supports all features exposed by the pyvesync 3.0 library.

## Features

| Entity | Type | Description |
|---|---|---|
| `fan.<device>` | Fan | On/off, fan speed (1–4), preset modes |
| `sensor.<device>_air_quality` | Sensor | excellent / good / moderate / poor / very_poor |
| `sensor.<device>_pm25` | Sensor | PM2.5 concentration in µg/m³ |
| `sensor.<device>_filter_life` | Sensor | Filter remaining life (%) |
| `switch.<device>_display` | Switch | Screen on/off |
| `switch.<device>_child_lock` | Switch | Child lock on/off |
| `switch.<device>_light_detection` | Switch | Auto-dim based on ambient light |
| `select.<device>_auto_preference` | Select | Auto mode profile: default / efficient / quiet |

**Preset modes:** `manual`, `auto`, `sleep`, `pet`

## Installation via HACS

1. Open HACS in Home Assistant
2. Go to **Integrations → ⋮ → Custom Repositories**
3. Add your repository URL with category **Integration**
4. Click **Download** on the Levoit Vital 200S card
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration**
7. Search for **Levoit Vital 200S**

## Manual Installation

1. Download or clone this repository
2. Copy the `custom_components/levoit_vital200s/` folder into your HA `config/custom_components/` directory
3. Restart Home Assistant
4. Add the integration via **Settings → Devices & Services**

## Configuration

Enter your **VeSync account email and password** when prompted. The integration will find all Vital 200S devices on your account automatically.

Optionally set your **time zone** (e.g. `America/New_York`) — defaults to `America/New_York`.

## Requirements

- Home Assistant 2023.1 or newer
- `pyvesync >= 3.0.0` (installed automatically)
- Devices must be registered in the VeSync app first

## Notes

- This integration is **completely independent** of the built-in VeSync integration — both can run simultaneously without conflict
- State polls every **30 seconds**
- Fan speed changes update the UI **immediately** (optimistic update) without waiting for the next poll
- `fan_set_level` (the commanded speed) is used for percentage display rather than `fan_level` (the live reported level), which avoids the "snapping back to 0" bug seen in other integrations
