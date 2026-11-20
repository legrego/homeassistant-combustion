# 🔥 Combustion BLE Integration

Integrate [Combustion](https://combustion.inc) Predictive Thermometers and Giant Grill Gauges into Home Assistant.

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]

**This integration will set up the following entities.**

Device | Entities
-- | --
Predictive Thermometer | Core, surface, and ambient temperature. Instant read temperature, while the probe is in instant read mode. Low battery. Each of the 8 thermistors and RSSI (disabled by default).
Giant Grill Gauge | Temperature (unknown when no sensor is plugged in). Low battery. RSSI (disabled by default).

The integration only listens to Bluetooth advertisements. It never connects to your devices, so it doesn't use up the connections your phone or Display need. It works with any Bluetooth adapter or proxy that Home Assistant supports, including passive ones.

Requires Home Assistant 2026.10.4 or newer.

## Installation

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
1. If you do not have a `custom_components` directory (folder) there, you need to create it.
1. In the `custom_components` directory (folder) create a new folder called `combustion`.
1. Download _all_ the files from the `custom_components/combustion/` directory (folder) in this repository.
1. Place the files you downloaded in the new directory (folder) you created.
1. Restart Home Assistant
1. Ensure you have a Combustion device turned on, and within bluetooth range of Home Assistant .
1. In the HA UI go to "Configuration" -> "Integrations" to see your discovered Combustion device.

## Configuration

There is no configuration. Once Home Assistant hears a Combustion device, it offers to set up the integration on the Integrations page. You can also add it from there yourself. One setup covers every Combustion device in range, and new devices are added automatically as they are heard.

## Supported devices

This integration reads data from Combustion's [Predictive Thermometer](https://combustion.inc/products/predictive-thermometer) and Giant Grill Gauge.

It can hear a probe directly, or through a MeatNet repeater such as the [Range-Extending Booster](https://combustion.inc/products/long-range-predictive-thermometer), [Range-Extending Display](https://combustion.inc/products/range-extending-display), or Giant Grill Gauge. When both are available, it uses the probe's own advertisements.

## How data is updated

Probes, gauges, and repeaters broadcast their readings several times a second, and entities update as soon as a reading changes. Nothing is polled.

A device becomes unavailable when it hasn't been heard, directly or through a repeater, for 15 seconds. Probes don't broadcast while they're in their charger, so they show as unavailable there.

## Known limitations

- Prediction and cook data need a Bluetooth connection, so they aren't available yet.
- Repeaters don't get entities of their own.
- The instant read temperature is in whole degrees Celsius, smoothed the way Combustion's apps show it. In Fahrenheit it changes in steps of about 2 °F.
- RSSI is only known for probes heard directly.

## Troubleshooting

- Check that Home Assistant's Bluetooth integration is set up, with an adapter or proxy in range of a probe or a repeater.
- Take the probe out of its charger.
- For debug logs, add this to `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.combustion: debug
      combustion_ble: debug
  ```

## Removing the integration

1. Go to **Settings** > **Devices & services**, select **Combustion**, open the three-dot menu, and select **Delete**.
1. If you installed it by hand, delete the `custom_components/combustion` folder and restart Home Assistant.

To remove a single probe or gauge you no longer use, delete it from its device page. This works for devices that haven't been heard since Home Assistant last started.

## Upgrading

Upgrading from earlier versions keeps your existing probe entities, their entity IDs, and their history. Some details change:

- Entity names use sentence case, for example "Core temperature".
- The battery sensor moves to the device's **Diagnostic** section.
- The `raw_advertisement_bytes` attribute on temperature sensors has been removed.

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

## Credits

Combustion, Inc. for their wonderful [documentation](https://github.com/combustion-inc/combustion-documentation) and [code samples](https://github.com/combustion-inc/combustion-ios-ble).


***

[combustion]: https://combustion.inc/
[license-shield]: https://img.shields.io/github/license/legrego/homeassistant-combustion.svg?style=flat
[maintenance-shield]: https://img.shields.io/badge/maintainer-Larry%20Gregory%20@legrego-blue.svg?style=flat
[releases-shield]: https://img.shields.io/github/release/legrego/homeassistant-combustion.svg?style=flat
[releases]: https://github.com/legrego/homeassistant-combustion/releases
