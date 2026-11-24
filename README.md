# 🔥 Combustion BLE Integration

Integrate [Combustion](https://combustion.inc) Predictive Thermometers and Giant Grill Gauges into Home Assistant.

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]

**This integration will set up the following entities.**

Device | Entities
-- | --
Predictive Thermometer | Core, surface, and ambient temperature. Instant read temperature, while the probe is in instant read mode. Low battery. Each of the 8 thermistors and RSSI (disabled by default). Optional cooking target (settable), time remaining, and prediction status, and SafeCook food safety (configurable).
Giant Grill Gauge | Temperature (unknown when no sensor is plugged in). Low battery. RSSI (disabled by default).

By default, the integration listens to Bluetooth advertisements without connecting to your devices. Temperature and battery sensors work with any Bluetooth adapter or proxy Home Assistant supports, including passive ones. Optional cooking predictions and SafeCook use one Bluetooth connection to a MeatNet repeater when one is in range, or a direct connection to each probe otherwise. An optional [MeatNet Cloud account](#meatnet-cloud) shows probes that are out of Bluetooth range.

Requires Home Assistant 2026.10.4 or newer.

## Installation

### Prerequisites

- Home Assistant 2026.10.4 or newer.
- Home Assistant's [Bluetooth integration](https://www.home-assistant.io/integrations/bluetooth/) set up with a supported adapter or Bluetooth proxy.
- A supported Combustion device, or a MeatNet repeater relaying it, within Bluetooth range of that adapter or proxy. Take probes out of their chargers so they broadcast.

### Install the files

1. Download this repository and copy the entire `custom_components/combustion` folder, including its subfolders, into your Home Assistant configuration directory (the directory containing `configuration.yaml`). Create `custom_components` if it does not exist.
1. Restart Home Assistant.

### Set up in Home Assistant

1. Go to **Settings** > **Devices & services**.
1. If **Combustion** appears under **Discovered**, select **Add** and confirm setup.
1. Otherwise, select **Add integration**, search for **Combustion**, select it, and confirm setup.

One setup covers every Combustion device in range. New devices are added automatically as they are heard. Manual setup can finish before a device is broadcasting; entities appear once Home Assistant receives its advertisements.

## Configuration

No address, account, or credentials are required. The integration listens for advertisements through Home Assistant's Bluetooth integration. Two things are optional, under **Settings > Devices & services > Combustion > Configure**:

Option | Description
-- | --
Cooking predictions and SafeCook | Connects over Bluetooth for [predictions](#cooking-predictions), SafeCook, and cooking targets. Off by default.
Link a MeatNet Cloud account | Signs in with an email and password, to show [linked probes](#meatnet-cloud) that are out of Bluetooth range. Nothing is linked by default.

The integration provides sensor and binary sensor entities, and, with cooking predictions on, a cooking target number and a cancel button. It does not define its own actions, triggers, or conditions; use the entities with Home Assistant's standard triggers, conditions, and actions, such as `number.set_value` and `button.press`.

## Supported devices

This integration reads data from Combustion's [Predictive Thermometer](https://combustion.inc/products/predictive-thermometer) and Giant Grill Gauge.

It can hear a probe directly, or through a MeatNet repeater such as the [Range-Extending Booster](https://combustion.inc/products/long-range-predictive-thermometer), [Range-Extending Display](https://combustion.inc/products/range-extending-display), or Giant Grill Gauge. When both are available, it uses the probe's own advertisements.

## How data is updated

Probes, gauges, and repeaters broadcast their readings several times a second, and entities update as soon as a reading changes. Readings are never polled; only [firmware and hardware versions](#firmware-and-hardware-versions) are read on a schedule.

A device becomes unavailable when it hasn't been heard, directly or through a repeater, for 2 minutes, unless [MeatNet Cloud](#meatnet-cloud) reports it. Home Assistant passes along each device's advertisements from only one Bluetooth adapter or proxy at a time, which can go a minute or more between them when its signal is weak. Probes don't broadcast while they're in their charger, so they show as unavailable there.

## Firmware and hardware versions

Each probe's and Giant Grill Gauge's device page shows its firmware and hardware versions. Advertisements don't include them, so the integration reads them over a connection: soon after a device is first heard while it's online, then once a day while it stays online. Devices that are only on while you cook are read the next time they're in use. If a read fails, the next route is tried, such as another repeater or the probe itself; once every route has failed, that device is tried again after 15 minutes. Home Assistant keeps the last versions read, so they still show while a device is off.

When cooking predictions are on, their connections also read versions. Otherwise the integration connects briefly, reads the versions, and disconnects: to a repeater that relays the probe, which reads every probe it relays in one connection, or else to the probe itself, using one of its three connection slots for a few seconds. Only one such connection is made at a time, and it gives way when cooking predictions need that device. Reading versions needs a Bluetooth adapter or proxy that supports active connections; with only passive adapters, versions stay unknown and everything else works as usual.

## Cooking predictions

Cooking predictions are optional and disabled by default. Open **Settings > Devices & services > Combustion > Configure**, select **Cooking predictions and SafeCook**, and enable **Enable cooking predictions and SafeCook**. The integration reloads and adds, for each probe, a cooking target temperature you can set, a button to cancel it, time remaining and prediction status sensors, and [SafeCook sensors and controls](#safecook-food-safety). Disabling the option releases the connections and removes these entities, other than sensors that a linked [MeatNet Cloud account](#meatnet-cloud) also provides.

This requires a Bluetooth adapter or proxy that supports active connections. Existing passive temperature sensors continue to work without connections.

When a MeatNet repeater (a Range-Extending Booster, Range-Extending Display, Giant Grill Gauge, or Engine) is in range of a connection-capable adapter, the integration connects to it. That one connection provides predictions for every probe on the repeater's network, including probes out of Home Assistant's direct range. It prefers a repeater that is relaying probes, then the strongest signal; if a repeater can't be connected, the next attempt tries another one.

Probes the repeater isn't reporting, or every probe when no repeater is available, get a direct connection instead, with the probe itself in range. Each probe supports three simultaneous connections; a direct connection uses one. Once the repeater reports a probe, its direct connection is released, so a probe may briefly have both connections at startup. If the repeater connection is lost, probes in direct range reconnect directly.

Time remaining stays unknown until the probe provides a usable prediction, then counts down in seconds. Repeaters relay each probe's status about every 5 seconds; direct connections update about every second. Prediction status distinguishes warming, predicting, and ready. Missing status updates expire after 15 seconds; disconnects clear predictions immediately. When a working connection drops, the integration reconnects after about 5 seconds; failed connections retry after at least 60 seconds. Direct connections also reconnect when another direct advertisement is received.

### Setting the cooking target

Set **Cooking target temperature** to start a removal prediction, and press **Cancel cooking target** to turn it off. Both work in dashboards and automations, through the standard `number.set_value` and `button.press` actions, and the Combustion app or Display can still change the target too. Targets range from 0 to 102.3 °C (32 to 216.1 °F), in steps of 0.1 °C.

Commands go through the connection that reports the probe's predictions: the repeater, or the direct connection. Both entities are unavailable while no connection reports the probe. The target shows the value the probe reports, so it updates with the probe's next status, within about 5 seconds through a repeater, rather than when the command is sent. If the probe or repeater rejects a command, or doesn't acknowledge it in time, the action fails with an error. A failed command over a direct connection also reconnects it.

The cooking target used to be a read-only sensor. It is now a number entity, so dashboards and automations that used `sensor.<probe>_cooking_target_temperature` need to use `number.<probe>_cooking_target_temperature` instead.

### Firmware and hardware versions

Prediction connections also read firmware and hardware versions, shown on each probe's and Giant Grill Gauge's device page. A direct connection reads its probe's versions; a repeater connection reads the versions of every probe it relays, once per connection, and its own when it is a Giant Grill Gauge. Versions stay unknown until a connection has read them. Home Assistant keeps the last versions read, including after a restart or with predictions turned off.

### SafeCook food safety

The same connection reports each probe's SafeCook program and its progress. Programs can be chosen in the Combustion app or Display, or [from Home Assistant](#configuring-safecook).

Sensor | Description
-- | --
Food safety | Off, not safe, safe, or safety impossible. Use it to trigger automations when food becomes safe.
Food safety threshold temperature | The program's threshold temperature. Unknown for Simplified programs that leave the threshold to the probe, such as those set from Home Assistant.
Time above food safety threshold | How long the core has been above the threshold.
Food safety log reduction | Integrated mode only: the pathogen log reduction so far.
Food safety target log reduction | Integrated mode only: the log reduction that makes the food safe.

All of them are unknown until a connection, or [MeatNet Cloud](#meatnet-cloud), reports SafeCook data, and on probe firmware that doesn't support SafeCook. When SafeCook is turned off, food safety is **Off** and the other SafeCook sensors are unknown. The program's product and serving are [selects](#configuring-safecook).

### Configuring SafeCook

Each probe has two selects:

Select | Description
-- | --
Food safety product | The program's product. Choosing a product starts a Simplified program for it, keeping the current serving (served immediately if SafeCook is off).
Food safety serving | How the food will be served. Choosing one restarts the current Simplified program with it.

They show every product a probe can report, so programs chosen in the app show too. Integrated products, and the Simplified Default product, can't be started from Home Assistant yet: choosing one, or changing the serving of such a program, fails with an error explaining why. Changing either select replaces the program and restarts its progress, so choosing a product and then a serving restarts it twice; the action below sets both at once.

The product and serving used to be read-only diagnostic sensors. Dashboards and automations that used `sensor.<probe>_food_safety_product` or `sensor.<probe>_food_safety_serving` need to use `select.<probe>_food_safety_product` or `select.<probe>_food_safety_serving` instead.

The **Configure SafeCook** action (`combustion.configure_safecook`) starts a Simplified program on a probe. Choose the probe, the product, and how it will be served (served immediately by default). The probe applies USDA guidelines for the product itself, as it does for programs chosen in Combustion's apps. Configuring replaces the probe's current program and restarts its progress.

```yaml
action: combustion.configure_safecook
data:
  device_id: 0123456789abcdef0123456789abcdef
  product: simplified_beef_cuts
  serving: served_immediately
```

`product` takes the same values as the Food safety product select for Simplified programs, except `simplified_default`: without parameters, the Default product served immediately is indistinguishable from SafeCook being off. Integrated programs, which need per-product parameters, can still be chosen in the Combustion app or Display.

Each probe also gets two buttons:

Button | Description
-- | --
Reset food safety progress | Restarts the program's progress, keeping the program. If the food already meets the program's conditions, it may report safe again right away.
Turn off food safety | Turns SafeCook off, as turning it off in the app does.

Commands go through the same connection as cooking targets and behave the same way: the selects and buttons are unavailable while no connection reports the probe, the selects and sensors update with the probe's next status, and a rejected or unacknowledged command fails with an error. Probe firmware without SafeCook support rejects them.

### Capture a local prediction report

With this repository's dependencies installed, run from the repository root:

```sh
poetry run python scripts/capture_predictions.py --seconds 120
```

To capture every probe a MeatNet repeater relays instead, add `--repeater` (or `--repeater ADDRESS` to choose one listed by `--list`). Label a capture with `--note`, for example `--note "SafeCook never set up"`.

Take the probe out of its charger, keep it near the computer, and set a cooking target in the Combustion app or Display first. On macOS, allow Bluetooth access for your terminal if prompted. If multiple probes are found, rerun with `--serial 10001CCC`, using your probe's serial. Use `--list` to discover probes and repeaters without connecting.

The script prints raw and decoded status notifications and saves them to a timestamped `.jsonl` report in the temporary directory; the full path is printed at the end. Share that report to check the packet format, prediction state, target, time remaining, SafeCook program and progress, update gaps, and connection behavior. Its summary lists every distinct SafeCook program seen for each probe. It reads notifications without changing the cooking target or SafeCook program. An idle or warming probe may have no usable time prediction; capturing during an active prediction is most useful. This checks local probe communication and decoding; Home Assistant dashboard and automation behavior still need testing in Home Assistant.

## MeatNet Cloud

Optional and experimental. [MeatNet Cloud](https://combustion.inc/pages/features/meat-net-cloud-watch-your-cook-from-anywhere) is Combustion's service for watching a cook from anywhere. With an account linked, probes keep reporting when they are out of Bluetooth range, as long as a WiFi accessory, or a phone running the Combustion app, uploads their readings.

Combustion doesn't document the service, so they can change it without notice. Bluetooth works the same with or without it, including while it is down or rejects the account. Without an account linked, nothing is sent to or read from the internet.

### Linking an account

Open **Settings > Devices & services > Combustion > Configure**, select **Link a MeatNet Cloud account**, and sign in with the email and password of your Combustion app account. That is separate from a Combustion store account, and Google and Apple sign-in aren't supported. The integration stores the email, an account ID, and a token that keeps it signed in. It doesn't store the password.

Every probe linked to the account then appears, even before Bluetooth has heard it. Probes that haven't been used recently show as unavailable. Each probe has one device and one set of entities, whichever source reports it, so history continues. Probes also get the time remaining, prediction status, and SafeCook sensors, whether or not cooking predictions are enabled over Bluetooth.

If the service stops accepting the account, Home Assistant asks you to sign in again. To stop using it, select **Unlink the MeatNet Cloud account** in the same place. Devices are kept, and entities that only the cloud provides are removed.

### Which source is used

Data | Source
-- | --
Temperatures and battery | Bluetooth, if it reported temperatures in the last 30 seconds. Otherwise the newest of either source, until it is 2 minutes old. Instant read broadcasts don't include these temperatures.
Time remaining, prediction status, and SafeCook sensors | A Bluetooth prediction connection that reports the probe. Otherwise the cloud, if its sample is less than 30 seconds old.
Firmware and hardware versions | Either source. The cloud's are used only from recent samples.
Instant read temperature and RSSI | Bluetooth only.
Cooking target, SafeCook selects, buttons, and the Configure SafeCook action | Bluetooth only. They are unavailable unless a prediction connection reaches the probe.

The cloud usually sends a sample every 5 seconds. Samples are judged by when they were taken, not when they arrived, so a wrong clock on the phone or accessory that uploads them, or in Home Assistant, can make them look too old to use. Samples dated more than a minute ahead are ignored.

To see which source a probe's temperatures come from, enable its **Data source** sensor, which is disabled by default.

## Known limitations

- Only Simplified SafeCook programs can be configured from Home Assistant. Integrated programs set in the app are reported, and can be reset or turned off.
- Only removal targets can be set. Removal-and-resting predictions set in the app are reported, but can't be set from Home Assistant.
- Repeaters don't get entities of their own.
- The instant read temperature is in whole degrees Celsius, smoothed the way Combustion's apps show it. In Fahrenheit it changes in steps of about 2 °F.
- RSSI is only known for probes heard directly.
- MeatNet Cloud is only used for probes. Giant Grill Gauges are read over Bluetooth.
- MeatNet Cloud doesn't say whether a probe is in instant read mode, and what it reports for a probe that is hasn't been checked.
- Predictions and SafeCook progress from MeatNet Cloud follow the format Combustion's app uses, but haven't been checked during a cook.

## Troubleshooting

- Check that Home Assistant's Bluetooth integration is set up, with an adapter or proxy in range of a probe or a repeater.
- Take the probe out of its charger.
- To report a problem, open **Settings > Devices & services > Combustion**, choose **Download diagnostics** from the three-dot menu, and attach the file. It leaves out serial numbers, Bluetooth addresses, and account details.
- For debug logs, add this to `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.combustion: debug
      combustion_ble: debug
      combustion_cloud: debug
  ```

- If MeatNet Cloud doesn't show a probe, check that the Combustion app shows it away from home, then check `cloud` in the diagnostics. `sample_age` is how many seconds ago its last reading was taken.

## Removing the integration

1. Go to **Settings** > **Devices & services**, select **Combustion**, open the three-dot menu, and select **Delete**.
1. If you installed it by hand, delete the `custom_components/combustion` folder and restart Home Assistant.

To remove a single probe or gauge you no longer use, delete it from its device page. This works for devices that haven't been heard since Home Assistant last started, and aren't linked to your MeatNet Cloud account.

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
