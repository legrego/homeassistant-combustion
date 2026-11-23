"""Diagnostics describe devices without identifying them or their owner."""

import json

import pytest
from combustion_cloud import DeviceIdentity, PermissionDeniedError
from homeassistant.core import HomeAssistant

from custom_components.combustion.const import CONF_ENABLE_PREDICTIONS
from custom_components.combustion.diagnostics import async_get_config_entry_diagnostics
from tests.test_food_safety import connect_direct
from tests.test_init import setup_entry
from tests.test_prediction import connection, food_safe_bytes  # noqa: F401 - fixture
from tests.utils.bt_utils import (
    GAUGE_ADDRESS,
    NODE_ADDRESS,
    PROBE_ADDRESS,
    gauge_payload,
    inject_bt_advertisement,
    node_payload,
    probe_payload,
    service_info,
)
from tests.utils.cloud_utils import (
    ACCOUNT_ID,
    CLOUD_DATA,
    EMAIL,
    OTHER_IDENTITY,
    REFRESH_TOKEN,
    FakeCloud,
    snapshot,
    status_data,
    status_event,
)


async def get_diagnostics(hass: HomeAssistant, entry) -> dict:
    """Return diagnostics as a user downloads them."""
    return json.loads(json.dumps(await async_get_config_entry_diagnostics(hass, entry)))


async def test_bluetooth(hass: HomeAssistant, connection) -> None:  # noqa: F811
    """Without an account, only Bluetooth is described."""
    entry, _, send = await connect_direct(hass, connection)
    await send(food_safe=food_safe_bytes())
    inject_bt_advertisement(hass, service_info(gauge_payload(), address=GAUGE_ADDRESS))
    await hass.async_block_till_done()

    result = await get_diagnostics(hass, entry)
    assert result["options"] == {CONF_ENABLE_PREDICTIONS: True}
    assert result["cloud"] is None
    bluetooth = result["bluetooth"]
    assert bluetooth["direct_connections"] == 1
    assert not bluetooth["repeater_connected"]
    assert bluetooth["repeaters_heard"] == 1  # Gauges are repeaters too.
    probe, gauge = bluetooth["devices"].values()
    assert probe["available"]
    assert probe["hop_count"] is None
    assert probe["readings"]["core_thermistor"] == 1
    assert probe["prediction"]["status"] == "predicting"
    assert probe["food_safety"]["status"] == "not_safe"
    assert gauge["model"] == "Giant Grill Gauge"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cloud_before_connecting(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """An account that hasn't been read yet has no devices."""
    entry = await setup_entry(hass, data=CLOUD_DATA)
    result = await get_diagnostics(hass, entry)
    assert result["cloud"] == {
        "connected": False,
        "last_error": None,
        "snapshot_age": None,
        "linked_devices": None,
        "skipped_associations": None,
        "unreadable_devices": {},
        "devices": {},
    }


async def test_cloud(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Both sources name a device alike, and nothing identifies the user."""
    gauge = DeviceIdentity("GG00000001", "GAUGE")
    unknown_safecook = status_data(OTHER_IDENTITY, age=100)
    unknown_safecook["food_safe_data"] = unknown_safecook["food_safe_status"] = None
    del unknown_safecook["collected_timestamp"]
    cloud.snapshot = snapshot(
        status_data(age=100),
        unknown_safecook,
        errors={gauge: PermissionDeniedError(403)},
        skipped_associations=1,
    )
    entry = await setup_entry(hass, data=CLOUD_DATA)
    for info in (
        service_info(probe_payload()),
        service_info(gauge_payload(), address=GAUGE_ADDRESS),
        service_info(node_payload(), address=NODE_ADDRESS),
    ):
        inject_bt_advertisement(hass, info)
    await hass.async_block_till_done()
    await cloud.connect()
    await cloud.send(status_event(temperature=50.0))

    result = await get_diagnostics(hass, entry)
    assert list(result["bluetooth"]["devices"]) == ["device 1", "device 2"]
    details = result["cloud"]
    assert details["connected"]
    assert details["snapshot_age"] == pytest.approx(0, abs=5)
    assert details["linked_devices"] == 3
    assert details["skipped_associations"] == 1
    # The gauge heard over Bluetooth.
    assert details["unreadable_devices"] == {"device 2": "PermissionDeniedError"}
    assert list(details["devices"]) == ["device 1", "device 3"]
    live = details["devices"]["device 1"]
    assert live["sample_age"] == pytest.approx(0, abs=5)
    assert live["temperatures"][0] == 50
    assert live["prediction"]["remaining_seconds"] == 600
    assert live["food_safety"]["program_set"]
    assert live["food_safety_state"] == "NOT_SAFE"
    assert "collected_timestamp" in live["fields"]
    stale = details["devices"]["device 3"]
    assert stale["sample_age"] is None
    assert stale["published_age"] == pytest.approx(99, abs=5)
    assert stale["food_safety"] is stale["food_safety_state"] is None

    text = json.dumps(result).lower()
    for secret in (
        ACCOUNT_ID,
        EMAIL,
        REFRESH_TOKEN,
        "synthetic",
        "10001ccc",
        OTHER_IDENTITY.serial_number,
        "gg00000001",
        "cr10000001",
        PROBE_ADDRESS,
        NODE_ADDRESS,
    ):
        assert secret.lower() not in text
