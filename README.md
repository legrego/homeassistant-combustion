# Combustion BLE Integration

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

![Project Maintenance][maintenance-shield]

_Integration to integrate with [combustion][combustion]._

**This integration will set up the following platforms.**

Platform | Description
-- | --
`binary_sensor` | _Coming Soon_ Show battery status from nodes on your Meatnet.
`sensor` | Show temperature data from devices on your Meatnet.

## Installation

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
1. If you do not have a `custom_components` directory (folder) there, you need to create it.
1. In the `custom_components` directory (folder) create a new folder called `combustion`.
1. Download _all_ the files from the `custom_components/combustion/` directory (folder) in this repository.
1. Place the files you downloaded in the new directory (folder) you created.
1. Restart Home Assistant
1. Ensure you have a Combustion device turned on, and within bluetooth range of Home Assistant .
1. In the HA UI go to "Configuration" -> "Integrations" to see your discovered Combustion device.

## Configuration is done in the UI

<!---->

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

***

[combustion]: https://combustion.inc/
[commits-shield]: https://img.shields.io/github/commit-activity/y/legrego/homeassistant-combustion.svg?style=for-the-badge
[commits]: https://github.com/legrego/homeassistant-combustion/commits/main
[license-shield]: https://img.shields.io/github/license/legrego/homeassistant-combustion.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-Larry%20Gregory%20@legrego-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/legrego/homeassistant-combustion.svg?style=for-the-badge
[releases]: https://github.com/legrego/homeassistant-combustion/releases
