# <picture style="width: 1em;"><img src="assets/icon.webp" alt="Combustion Icon" style="width: 1em;"/></picture> Combustion BLE Integration

Integrate [Combustion](https://combustion.inc) predictive probes into Home Assistant.

[![Combustion logo](assets/logo.webp)](https://combustion.inc)

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]

**This integration will set up the following platforms.**

Platform | Description
-- | --
`binary_sensor` | Show battery status from probes on your Meatnet.
`sensor` | Show temperature data from probes on your Meatnet.

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

There is currently no configuration required for this integration. Once the integration discovers your Combustion device(s), it will prompt you to add them on the Integrations page.

## Supported devices

This integration supports reading temperature and battery data from Combustion's [Predictive Thermometer](https://combustion.inc/products/predictive-thermometer).

This integration can read data from a probe directly, or via a Meatnet repeater such as the [Range-Extending Booster](https://combustion.inc/products/long-range-predictive-thermometer) or [Range-Extending Display](https://combustion.inc/products/range-extending-display).

This integration will not display information about the repeater itself, only the probes connected to it.

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

***

[combustion]: https://combustion.inc/
[license-shield]: https://img.shields.io/github/license/legrego/homeassistant-combustion.svg?style=flat
[maintenance-shield]: https://img.shields.io/badge/maintainer-Larry%20Gregory%20@legrego-blue.svg?style=flat
[releases-shield]: https://img.shields.io/github/release/legrego/homeassistant-combustion.svg?style=flat
[releases]: https://github.com/legrego/homeassistant-combustion/releases
