"""Entities of probes linked to a MeatNet Cloud account."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from combustion_cloud import ApiError, CloudEvent, DeviceIdentity
from homeassistant.const import (
    ATTR_DEVICE_ID,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.combustion import async_remove_config_entry_device
from custom_components.combustion.const import CONF_ENABLE_PREDICTIONS, DOMAIN
from tests.conftest import FakeClock
from tests.test_init import PROBE, get_state, setup_entry
from tests.utils.bt_utils import (
    GAUGE_ADDRESS,
    gauge_payload,
    inject_bt_advertisement,
    probe_payload,
    service_info,
)
from tests.utils.cloud_utils import (
    CLOUD_DATA,
    OTHER_IDENTITY,
    PROBE_IDENTITY,
    FakeCloud,
    settle_hass,
    snapshot,
    status_data,
    status_event,
)

OTHER = "c0ffee1"
CORE = f"{PROBE}--sensor--core"
CLOUD_SENSORS = {
    f"{PROBE}--{key}"
    for key in (
        "prediction_remaining",
        "prediction_status",
        "food_safety",
        "food_safety_threshold",
        "food_safety_time_above_threshold",
        "food_safety_log_reduction",
        "food_safety_target_log_reduction",
        "data_source",
    )
}

ENABLED_DEFAULT = Entity.entity_registry_enabled_default

PREFERENCES = CloudEvent.from_data({"type": "user_preferences_update"})


@pytest.fixture(autouse=True)
def no_pacing():
    """Read the account as soon as it changes, and enable every entity."""
    with (
        patch("custom_components.combustion.cloud.SNAPSHOT_INTERVAL", 0),
        patch("custom_components.combustion.cloud.INITIAL_RETRY_DELAY", 60),
        patch.object(Entity, "entity_registry_enabled_default", True),
    ):
        yield


@pytest.fixture
def clock(freezer, clock: FakeClock) -> FakeClock:
    """Control the Bluetooth clock too. The wall clock has to be stopped first."""
    return clock


async def setup_cloud(hass: HomeAssistant, cloud: FakeCloud, *, options=None):
    """Set up the integration with a connected account."""
    entry = await setup_entry(hass, data=CLOUD_DATA)
    if options is not None:
        hass.config_entries.async_update_entry(entry, options=options)
        await settle_hass(hass)
    await cloud.connect()
    await settle_hass(hass)
    return entry


async def send(hass: HomeAssistant, cloud: FakeCloud, *events) -> None:
    """Deliver cloud events to entities."""
    await cloud.send(*events)
    await settle_hass(hass)


async def hear(hass: HomeAssistant, reading: float = 30.0, **payload) -> None:
    """Hear the probe over Bluetooth."""
    inject_bt_advertisement(
        hass, service_info(probe_payload(temperatures=[reading] * 8, **payload))
    )
    await hass.async_block_till_done()


async def pass_time(
    hass: HomeAssistant, clock: FakeClock, freezer, seconds: float
) -> None:
    """Let time pass for both sources, then run the integration's once-per-second tick."""
    clock.advance(seconds)
    freezer.tick(seconds)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1.1))
    await hass.async_block_till_done()


def state(hass: HomeAssistant, unique_id: str, platform: str = "sensor") -> str:
    """Return an entity's state."""
    return get_state(hass, platform, unique_id).state


def unique_ids(hass: HomeAssistant, entry) -> set[str]:
    """Return the unique IDs of registered entities."""
    return {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }


def get_device(hass: HomeAssistant, entry, identifier: str = PROBE):
    """Return a device's registry entry."""
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )


async def test_linked_probe(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A linked probe appears without ever being heard, with everything the cloud reports."""
    cloud.snapshot = snapshot(status_data(temperature=50.0))
    entry = await setup_cloud(hass, cloud)
    assert float(state(hass, CORE)) == 50
    assert get_state(hass, "sensor", CORE).attributes["thermistor_id"] == 1
    assert float(state(hass, f"{PROBE}--sensor--surface")) == 53
    assert float(state(hass, f"{PROBE}--sensor--ambient")) == 57
    assert float(state(hass, f"{PROBE}--thermistor--2")) == 51
    assert state(hass, f"{PROBE}--battery", "binary_sensor") == STATE_OFF
    assert state(hass, f"{PROBE}--prediction_status") == "predicting"
    assert float(state(hass, f"{PROBE}--prediction_remaining")) == pytest.approx(
        600, abs=2
    )
    assert state(hass, f"{PROBE}--food_safety") == "not_safe"
    assert state(hass, f"{PROBE}--data_source") == "cloud"
    # Only Bluetooth reports these.
    assert state(hass, f"{PROBE}--instant_read") == STATE_UNKNOWN
    assert state(hass, f"{PROBE}--rssi") == STATE_UNKNOWN
    # Controls need predictions over Bluetooth.
    assert not {f"{PROBE}--prediction_target"} & unique_ids(hass, entry)

    device = get_device(hass, entry)
    assert device.name == "Predictive Thermometer 10001CCC"
    assert device.serial_number == "10001CCC"
    assert device.model == "Predictive Thermometer"

    await send(hass, cloud, status_event(temperature=55.0))
    assert float(state(hass, CORE)) == 55
    assert (device.id, "v2.4.0", "v1.1-A1") == (
        (updated := get_device(hass, entry)).id,
        updated.sw_version,
        updated.hw_version,
    )


async def test_data_source_is_disabled_by_default(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """The data source is for diagnosing, so users choose to show it."""
    with patch.object(Entity, "entity_registry_enabled_default", ENABLED_DEFAULT):
        await setup_cloud(hass, cloud)
    registry = er.async_get(hass)
    entity = registry.async_get(
        registry.async_get_entity_id("sensor", DOMAIN, f"{PROBE}--data_source")
    )
    assert entity.disabled
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert state(hass, f"{PROBE}--prediction_status") == "predicting"


async def test_leading_zeros(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Unique IDs don't depend on how a serial number is written."""
    cloud.snapshot = snapshot(status_data(OTHER_IDENTITY))
    await setup_cloud(hass, cloud)
    assert OTHER_IDENTITY.serial_number == "0C0FFEE1"
    assert float(state(hass, f"{OTHER}--sensor--core")) == 20


async def test_stale_and_unread_probes(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Probes that were last reported long ago, or can't be read, appear as unavailable."""
    cloud.snapshot = snapshot(
        status_data(age=28 * 86400), errors={OTHER_IDENTITY: ApiError(404)}
    )
    entry = await setup_cloud(hass, cloud)
    for identifier in (PROBE, OTHER):
        assert state(hass, f"{identifier}--sensor--core") == STATE_UNAVAILABLE
        assert state(hass, f"{identifier}--data_source") == STATE_UNAVAILABLE
        assert get_device(hass, entry, identifier).sw_version is None
    await send(hass, cloud, status_event(OTHER_IDENTITY))
    assert float(state(hass, f"{OTHER}--sensor--core")) == 20
    assert state(hass, CORE) == STATE_UNAVAILABLE


async def test_handover(
    hass: HomeAssistant, cloud: FakeCloud, clock: FakeClock, freezer
) -> None:
    """Readings follow Bluetooth while it is heard, then the cloud, and Bluetooth again."""
    entry = await setup_cloud(hass, cloud)
    device_id = get_device(hass, entry).id
    entities = unique_ids(hass, entry)
    await hear(hass, 30)
    assert float(state(hass, CORE)) == 30
    assert state(hass, f"{PROBE}--data_source") == "bluetooth"
    assert float(state(hass, f"{PROBE}--rssi")) < 0
    # One device and one set of entities, whichever source reports it.
    assert get_device(hass, entry).id == device_id
    assert unique_ids(hass, entry) == entities

    await send(hass, cloud, status_event(temperature=50.0))
    assert float(state(hass, CORE)) == 30
    await pass_time(hass, clock, freezer, 29)
    await send(hass, cloud, status_event(temperature=50.0))
    assert float(state(hass, CORE)) == 30
    # Without any event, as time passes.
    await pass_time(hass, clock, freezer, 2)
    assert float(state(hass, CORE)) == 50
    assert float(state(hass, f"{PROBE}--thermistor--8")) == 57
    assert state(hass, f"{PROBE}--data_source") == "cloud"

    await hear(hass, 35)
    assert float(state(hass, CORE)) == 35
    assert state(hass, f"{PROBE}--data_source") == "bluetooth"

    # Neither is recent, so the newest is kept until it is stale.
    await pass_time(hass, clock, freezer, 100)
    assert float(state(hass, CORE)) == 35
    await pass_time(hass, clock, freezer, 21)
    assert state(hass, CORE) == STATE_UNAVAILABLE
    await send(hass, cloud, status_event(temperature=45.0))
    assert float(state(hass, CORE)) == 45
    # A signal strength from long ago isn't shown alongside cloud readings.
    assert state(hass, f"{PROBE}--rssi") == STATE_UNKNOWN


async def test_cloud_goes_quiet(
    hass: HomeAssistant, cloud: FakeCloud, clock: FakeClock, freezer
) -> None:
    """Countdowns continue, and each value expires, without any event from the cloud."""
    cloud.snapshot = snapshot(status_data())
    await setup_cloud(hass, cloud)
    assert float(state(hass, f"{PROBE}--prediction_remaining")) == 600
    await pass_time(hass, clock, freezer, 10)
    assert float(state(hass, f"{PROBE}--prediction_remaining")) == 590
    await pass_time(hass, clock, freezer, 20)
    assert state(hass, f"{PROBE}--prediction_remaining") == STATE_UNKNOWN
    assert state(hass, f"{PROBE}--prediction_status") == STATE_UNKNOWN
    assert state(hass, f"{PROBE}--food_safety") == STATE_UNKNOWN
    assert float(state(hass, CORE)) == 20
    await pass_time(hass, clock, freezer, 90)
    assert float(state(hass, CORE)) == 20
    await pass_time(hass, clock, freezer, 1)
    assert state(hass, CORE) == STATE_UNAVAILABLE


async def test_live_updates_before_the_account_is_read(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Live readings are shown while reading the account fails."""
    cloud.failures["get_snapshot"] = [ApiError(503)]
    entry = await setup_cloud(hass, cloud)
    await send(hass, cloud, status_event(temperature=50.0))
    assert entry.runtime_data.cloud.snapshot is None
    assert float(state(hass, CORE)) == 50
    device = get_device(hass, entry)
    assert not await async_remove_config_entry_device(hass, entry, device)

    cloud.snapshot = snapshot(status_data(OTHER_IDENTITY))
    await send(hass, cloud, PREFERENCES)
    assert float(state(hass, f"{OTHER}--sensor--core")) == 20
    assert state(hass, CORE) == STATE_UNAVAILABLE


async def test_controls_need_bluetooth(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """The cloud doesn't make controls available, and commands explain why they fail."""
    entry = await setup_cloud(hass, cloud, options={CONF_ENABLE_PREDICTIONS: True})
    assert float(state(hass, CORE)) == 20
    controls = [
        entity
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if entity.domain in ("button", "number", "select")
    ]
    assert len(controls) == 6
    for control in controls:
        assert hass.states.get(control.entity_id).state == STATE_UNAVAILABLE

    with pytest.raises(ServiceValidationError, match="over Bluetooth"):
        await hass.services.async_call(
            DOMAIN,
            "configure_safecook",
            {
                ATTR_DEVICE_ID: get_device(hass, entry).id,
                "product": "simplified_beef_cuts",
            },
            blocking=True,
        )
    # Even if one is reached anyway.
    for control in controls:
        entity = hass.data["entity_components"][control.domain].get_entity(
            control.entity_id
        )
        if control.domain == "button":
            command = entity.async_press()
        elif control.domain == "number":
            command = entity.async_set_native_value(60)
        else:
            assert entity.current_option is None
            command = entity.async_select_option(entity.options[1])
        with pytest.raises(HomeAssistantError) as err:
            await command
        assert err.value.translation_key == "not_connected"
        assert err.value.translation_placeholders == {"probe": "10001CCC"}


async def test_controls_need_recent_bluetooth(
    hass: HomeAssistant, cloud: FakeCloud, clock: FakeClock, freezer
) -> None:
    """Controls follow Bluetooth's availability, not the cloud's."""
    entry = await setup_cloud(hass, cloud, options={CONF_ENABLE_PREDICTIONS: True})
    await hear(hass)
    target = f"{PROBE}--prediction_target"
    with patch.object(type(entry.runtime_data), "can_command", return_value=True):
        await pass_time(hass, clock, freezer, 1)
        assert state(hass, target, "number") != STATE_UNAVAILABLE
        await pass_time(hass, clock, freezer, 121)
        await send(hass, cloud, status_event())
        assert float(state(hass, CORE)) == 20
        assert state(hass, target, "number") == STATE_UNAVAILABLE


async def test_heard_before_linked(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A probe already heard over Bluetooth gains cloud data without new entities."""
    entry = await setup_entry(hass, data=CLOUD_DATA)
    await hear(hass)
    entities = unique_ids(hass, entry)
    assert entities > CLOUD_SENSORS
    assert state(hass, f"{PROBE}--prediction_status") == STATE_UNKNOWN
    await cloud.connect()
    await settle_hass(hass)
    assert unique_ids(hass, entry) == entities
    assert state(hass, f"{PROBE}--prediction_status") == "predicting"
    assert len(entry.runtime_data.devices) == 1


async def test_only_linked_probes(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Gauges stay on Bluetooth, and probes that aren't linked aren't added."""
    gauge = DeviceIdentity("GG00000001", "GAUGE")
    cloud.snapshot = snapshot(status_data(gauge), status_data())
    entry = await setup_cloud(hass, cloud)
    await send(hass, cloud, status_event(OTHER_IDENTITY))
    assert list(entry.runtime_data.devices) == [PROBE]

    inject_bt_advertisement(hass, service_info(gauge_payload(), address=GAUGE_ADDRESS))
    await hass.async_block_till_done()
    assert float(state(hass, "GG00000001--temperature")) == 150
    assert "GG00000001--data_source" not in unique_ids(hass, entry)


async def test_unlinking_a_probe(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A probe unlinked in the app is kept, but no longer updated, and can be removed."""
    entry = await setup_cloud(hass, cloud)
    await send(hass, cloud, status_event(temperature=21.0))
    device = get_device(hass, entry)
    assert device.sw_version == "v2.4.0"
    assert not await async_remove_config_entry_device(hass, entry, device)

    # A list that couldn't be read in full doesn't mean it was unlinked.
    cloud.snapshot = snapshot(skipped_associations=1)
    await send(hass, cloud, PREFERENCES)
    assert float(state(hass, CORE)) == 21
    assert not await async_remove_config_entry_device(hass, entry, device)

    cloud.snapshot = snapshot()
    await send(hass, cloud, PREFERENCES)
    assert state(hass, CORE) == STATE_UNAVAILABLE
    # A late message doesn't bring it back.
    await send(hass, cloud, status_event())
    assert state(hass, CORE) == STATE_UNAVAILABLE

    assert await async_remove_config_entry_device(hass, entry, device)
    dr.async_get(hass).async_remove_device(device.id)
    await settle_hass(hass)
    assert not unique_ids(hass, entry)

    # Linking it again adds it afresh.
    cloud.snapshot = snapshot(status_data())
    await send(hass, cloud, PREFERENCES)
    assert float(state(hass, CORE)) == 20
    await send(hass, cloud, status_event(temperature=21.0))
    assert get_device(hass, entry).sw_version == "v2.4.0"


async def test_removing_heard_probe(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A probe heard over Bluetooth can't be removed, linked or not."""
    entry = await setup_cloud(hass, cloud)
    await hear(hass)
    cloud.snapshot = snapshot()
    await send(hass, cloud, PREFERENCES)
    assert float(state(hass, CORE)) == 30
    device = get_device(hass, entry)
    assert not await async_remove_config_entry_device(hass, entry, device)
    assert entry.runtime_data.devices


async def test_unlinking_the_account(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Unlinking the account keeps devices, and removes what only the cloud was reporting."""
    entry = await setup_cloud(hass, cloud)
    await hear(hass)
    assert unique_ids(hass, entry) > CLOUD_SENSORS
    hass.config_entries.async_update_entry(entry, data={})
    await settle_hass(hass)
    assert not CLOUD_SENSORS & unique_ids(hass, entry)
    await hear(hass, 40)
    assert float(state(hass, CORE)) == 40
    assert not CLOUD_SENSORS & unique_ids(hass, entry)


async def test_unlinking_the_account_with_predictions(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Sensors that Bluetooth predictions also report stay."""
    entry = await setup_cloud(hass, cloud, options={CONF_ENABLE_PREDICTIONS: True})
    hass.config_entries.async_update_entry(entry, data={})
    await settle_hass(hass)
    assert CLOUD_SENSORS & unique_ids(hass, entry) == CLOUD_SENSORS - {
        f"{PROBE}--data_source"
    }


async def test_turning_predictions_off(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """The cloud's sensors stay when Bluetooth predictions are turned off."""
    entry = await setup_cloud(hass, cloud, options={CONF_ENABLE_PREDICTIONS: True})
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: False}
    )
    await settle_hass(hass)
    await cloud.connect()
    await settle_hass(hass)
    assert unique_ids(hass, entry) > CLOUD_SENSORS
    assert f"{PROBE}--prediction_target" not in unique_ids(hass, entry)
    assert state(hass, f"{PROBE}--prediction_status") == "predicting"


async def test_revisions(
    hass: HomeAssistant, cloud: FakeCloud, clock: FakeClock, freezer
) -> None:
    """Revisions are recorded as each source's change, without the sources taking turns."""
    # Without another sample, or anything changing as this one ages.
    cloud.snapshot = snapshot(status_data() | {"prediction_mode": "NONE"})
    entry = await setup_cloud(hass, cloud)
    await pass_time(hass, clock, freezer, 1)
    assert get_device(hass, entry).sw_version == "v2.4.0"
    await hear(hass)
    (device,) = entry.runtime_data.devices.values()
    device.bluetooth.hardware_revision = "v9"
    await hear(hass, 31)
    assert get_device(hass, entry).hw_version == "v9"
    assert get_device(hass, entry).sw_version == "v2.4.0"
    for reading in (21.0, 22.0):
        await send(hass, cloud, status_event(temperature=reading))
        await hear(hass, reading)
    assert get_device(hass, entry).hw_version == "v9"
    # From before an update.
    await pass_time(hass, clock, freezer, 121)
    old = status_data(age=121) | {"firmware_version": "v1.0.0"}
    await send(
        hass,
        cloud,
        CloudEvent.from_data({"type": "probe_status_update", "data": old}),
    )
    assert get_device(hass, entry).sw_version == "v2.4.0"


async def test_availability_is_logged_once(
    hass: HomeAssistant,
    cloud: FakeCloud,
    clock: FakeClock,
    freezer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A linked probe is unavailable only once neither source reports it."""
    await setup_cloud(hass, cloud)
    await hear(hass)
    await pass_time(hass, clock, freezer, 121)  # Bluetooth and the cloud go quiet.
    await send(hass, cloud, status_event())
    await hear(hass)
    for _ in range(5):  # Bluetooth alone goes quiet.
        await pass_time(hass, clock, freezer, 30)
        await send(hass, cloud, status_event())
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Probe(")
    ]
    assert messages == [
        "Probe(10001CCC) is unavailable: not reported over Bluetooth or by MeatNet Cloud"
        " for 120 seconds",
        "Probe(10001CCC) is available again",
    ]


async def test_nothing_is_added_while_unloading(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Cloud updates are ignored once the integration is stopping."""
    entry = await setup_cloud(hass, cloud)
    coordinator = entry.runtime_data
    stop = coordinator.async_stop_connections

    async def link_another() -> None:
        await stop()
        coordinator.cloud._linked |= {OTHER_IDENTITY}
        coordinator.cloud._async_notify()

    with patch.object(coordinator, "async_stop_connections", link_another):
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert list(coordinator.devices) == [PROBE]
    assert coordinator.cloud._listeners == []


async def test_restart_without_internet(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Bluetooth works while the account can't be read, and cloud-only probes are unavailable."""
    cloud.snapshot = snapshot(status_data(), status_data(OTHER_IDENTITY))
    entry = await setup_cloud(hass, cloud)
    cloud.resume_gate.clear()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle_hass(hass)
    await hear(hass)
    assert float(state(hass, CORE)) == 30
    assert state(hass, f"{OTHER}--sensor--core") == STATE_UNAVAILABLE
    assert PROBE_IDENTITY not in entry.runtime_data.cloud.linked

    cloud.resume_gate.set()
    await cloud.connect()
    await settle_hass(hass)
    assert float(state(hass, f"{OTHER}--sensor--core")) == 20
