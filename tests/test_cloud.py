"""The MeatNet Cloud connection, and its isolation from Bluetooth."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from aiohttp import web
from combustion_cloud import (
    ApiError,
    AuthenticationError,
    ClientClosedError,
    CloudConnectionError,
    CloudEvent,
    Disconnected,
    PermissionDeniedError,
    ProtocolError,
    RateLimitError,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.combustion.cloud import (
    INITIAL_RETRY_DELAY,
    MAX_RETRY_DELAY,
    REFUSED_RETRY_DELAY,
    CloudManager,
)
from custom_components.combustion.const import (
    CONF_ACCOUNT_ID,
    CONF_CLOUD,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from tests.test_init import PROBE, get_state, setup_entry
from tests.utils.bt_utils import inject_bt_advertisement, probe_payload, service_info
from tests.utils.cloud_utils import (
    ACCOUNT_ID,
    CLOUD_DATA,
    EMAIL,
    OTHER_IDENTITY,
    PASSWORD,
    PROBE_IDENTITY,
    REFRESH_TOKEN,
    FakeCloud,
    FakeService,
    settle,
    settle_hass,
    snapshot,
    status_data,
    status_event,
    tokens,
)

MODULE = "custom_components.combustion.cloud"


@pytest.fixture
def no_delays() -> Generator[None]:
    """Retry at once."""
    with (
        patch(f"{MODULE}.INITIAL_RETRY_DELAY", 0),
        patch(f"{MODULE}.REFUSED_RETRY_DELAY", 0),
        patch(f"{MODULE}.SNAPSHOT_INTERVAL", 0),
    ):
        yield


async def setup_cloud(hass: HomeAssistant):
    """Set up the integration with an account linked."""
    entry = await setup_entry(hass, data=CLOUD_DATA)
    await settle()
    return entry


def reauth_flows(hass: HomeAssistant) -> list:
    """Return open requests to sign in again."""
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


def core_temperature(manager: CloudManager, identity=PROBE_IDENTITY) -> float | None:
    """Return the core temperature last reported for a probe."""
    return manager.store.devices[identity].virtual_temperature("core")


async def assert_bluetooth_works(hass: HomeAssistant, entry, reading: float) -> None:
    """Check that the integration is loaded and follows Bluetooth advertisements."""
    assert entry.state is ConfigEntryState.LOADED
    inject_bt_advertisement(
        hass, service_info(probe_payload(temperatures=[reading] * 8))
    )
    await hass.async_block_till_done()
    state = get_state(hass, "sensor", f"{PROBE}--sensor--core").state
    assert float(state) == pytest.approx(reading)


async def test_not_linked(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Without a linked account, nothing contacts the cloud."""
    entry = await setup_entry(hass)
    await assert_bluetooth_works(hass, entry, 30)
    assert entry.runtime_data.cloud is None
    assert not cloud.clients


@pytest.mark.usefixtures("no_delays")
async def test_follows_account(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A snapshot is read on connecting, and live updates follow."""
    entry = await setup_cloud(hass)
    manager = entry.runtime_data.cloud
    assert cloud.resumed == [REFRESH_TOKEN]
    assert not manager.connected
    assert not cloud.snapshots

    await cloud.connect()
    assert manager.connected
    assert cloud.snapshots == 1
    assert core_temperature(manager) == 20

    await cloud.send(status_event(temperature=50.0))
    assert core_temperature(manager) == 50
    # A delayed message doesn't replace a newer one.
    await cloud.send(status_event(temperature=40.0, age=30))
    assert core_temperature(manager) == 50
    # Other messages are ignored, and only changes to linked devices need a snapshot.
    await cloud.send(CloudEvent.from_data({"type": "future_update", "data": {}}))
    assert cloud.snapshots == 1

    cloud.snapshot = snapshot(status_data(OTHER_IDENTITY))
    await cloud.send(CloudEvent.from_data({"type": "user_preferences_update"}))
    assert cloud.snapshots == 2
    assert list(manager.store.devices) == [OTHER_IDENTITY]

    # A device missing from a list that couldn't be read in full may still be linked.
    cloud.snapshot = snapshot(skipped_associations=1)
    await cloud.send(CloudEvent.from_data({"type": "user_preferences_update"}))
    assert cloud.snapshots == 3
    assert list(manager.store.devices) == [OTHER_IDENTITY]

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert [client.closed for client in cloud.clients] == [True]
    assert not manager.connected


async def test_snapshots_are_paced(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A burst of preference changes doesn't cause a burst of reads."""
    await setup_cloud(hass)
    await cloud.connect()
    for _ in range(5):
        await cloud.send(CloudEvent.from_data({"type": "user_preferences_update"}))
    assert cloud.snapshots == 1


async def test_setup_does_not_wait(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Setup and unload don't depend on the cloud answering."""
    cloud.resume_gate.clear()
    entry = await setup_cloud(hass)
    assert cloud.resumed
    await assert_bluetooth_works(hass, entry, 30)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert cloud.clients[0].closed


FAILURES = [
    AuthenticationError("Rejected"),
    PermissionDeniedError(403),
    ApiError(400, "API_KEY_INVALID"),
    ApiError(503),
    RateLimitError(0),
    CloudConnectionError("Interrupted"),
    ProtocolError("Unreadable"),
    ClientClosedError("Closed"),
    RuntimeError("Bug"),
]


@pytest.mark.parametrize("failure", FAILURES, ids=lambda err: type(err).__name__)
@pytest.mark.parametrize("stage", ["resume", "stream", "get_snapshot"])
@pytest.mark.usefixtures("no_delays")
async def test_failures_leave_bluetooth_working(
    hass: HomeAssistant, cloud: FakeCloud, stage: str, failure: Exception
) -> None:
    """No cloud failure stops Bluetooth, and all but a rejected session recover by themselves."""
    if stage != "stream":
        cloud.failures[stage].append(failure)
    entry = await setup_cloud(hass)
    await assert_bluetooth_works(hass, entry, 30)
    await cloud.connect()
    if stage == "stream":
        await cloud.send(failure)
    await settle_hass(hass)
    coordinator = entry.runtime_data
    await assert_bluetooth_works(hass, entry, 40)

    rejected = isinstance(failure, AuthenticationError)
    assert bool(reauth_flows(hass)) == rejected
    if not rejected:
        await cloud.connect()
        await settle()
        assert coordinator.cloud.connected
        assert core_temperature(coordinator.cloud) == 20
        assert coordinator.cloud.last_error is None
    else:
        assert not coordinator.cloud.connected
        assert "Rejected" in coordinator.cloud.last_error

    # The entry was never reloaded, and every client but the one in use has been closed.
    assert entry.runtime_data is coordinator
    assert all(client.closed for client in cloud.clients[:-1])
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert all(client.closed for client in cloud.clients)


async def test_stream_continues_while_snapshots_fail(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A failing snapshot is retried without interrupting live updates."""
    cloud.failures["get_snapshot"] = [CloudConnectionError("Interrupted")]
    with patch(f"{MODULE}.INITIAL_RETRY_DELAY", 0.05):
        entry = await setup_cloud(hass)
        await cloud.connect()
        await cloud.send(status_event(temperature=50.0))
        manager = entry.runtime_data.cloud
        assert manager.connected
        assert manager.snapshot is None
        assert core_temperature(manager) == 50
        await asyncio.sleep(0.1)
    assert cloud.snapshots == 2
    assert manager.snapshot is cloud.snapshot
    assert len(cloud.clients) == 1


async def test_stream_ending_quietly_reconnects(
    hass: HomeAssistant, cloud: FakeCloud, no_delays: None
) -> None:
    """A stream that ends without an error is replaced."""
    entry = await setup_cloud(hass)
    await cloud.connect()
    await cloud.send(None)
    await cloud.connect()
    assert len(cloud.clients) == 2
    assert cloud.clients[0].closed
    assert entry.runtime_data.cloud.connected


async def test_outages_are_logged_once(
    hass: HomeAssistant, cloud: FakeCloud, caplog: pytest.LogCaptureFixture
) -> None:
    """An outage is logged once, and again if it worsens or ends."""
    entry = await setup_cloud(hass)
    await cloud.connect()
    manager = entry.runtime_data.cloud
    outage = Disconnected(datetime.now(UTC), "Interrupted", 1.0)
    await cloud.send(outage, outage)
    assert not manager.connected
    assert manager.last_error == "Interrupted"
    await cloud.connect()
    assert manager.connected
    await cloud.connect()  # Token renewal replaces the socket without an outage.
    await cloud.send(outage, PermissionDeniedError(403))

    messages = [
        (record.levelname, record.getMessage())
        for record in caplog.records
        if "MeatNet Cloud is" in record.getMessage()
    ]
    assert messages == [
        ("INFO", "MeatNet Cloud is unavailable: Interrupted"),
        ("INFO", "MeatNet Cloud is connected again"),
        ("INFO", "MeatNet Cloud is unavailable: Interrupted"),
        (
            "WARNING",
            "MeatNet Cloud is unavailable: PermissionDeniedError: Cloud service returned HTTP 403",
        ),
    ]


async def test_retry_delays(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Outages back off, and errors that retrying won't fix wait longer."""
    entry = await setup_cloud(hass)
    manager = entry.runtime_data.cloud
    interrupted = CloudConnectionError("Interrupted")
    delays = [manager._next_delay(interrupted) for _ in range(10)]
    assert delays[:3] == [5, 10, 20]
    assert delays[-1] == MAX_RETRY_DELAY
    assert manager._next_delay(RateLimitError(1000)) == 1000
    assert manager._next_delay(RateLimitError(1)) == MAX_RETRY_DELAY
    assert manager._next_delay(RateLimitError(None, status=400)) == MAX_RETRY_DELAY
    assert manager._next_delay(ApiError(502)) == MAX_RETRY_DELAY
    assert manager._next_delay(ApiError(408)) == MAX_RETRY_DELAY
    for refused in (ApiError(400), ApiError(404), PermissionDeniedError(403)):
        assert manager._next_delay(refused) == REFUSED_RETRY_DELAY
    # Connecting starts the backoff afresh.
    await cloud.connect()
    assert manager._next_delay(interrupted) == INITIAL_RETRY_DELAY


async def test_renewed_tokens_are_saved(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Renewing the session saves its token without reloading or reconnecting."""
    entry = await setup_cloud(hass)
    await cloud.connect()
    coordinator = entry.runtime_data
    renew = cloud.clients[0].on_token_update

    modified_at = entry.modified_at
    renew(tokens())
    assert entry.modified_at == modified_at  # Unchanged tokens aren't saved.

    renew(tokens("renewed"))
    await settle_hass(hass)
    assert entry.data[CONF_CLOUD] == {
        CONF_EMAIL: EMAIL,
        CONF_ACCOUNT_ID: ACCOUNT_ID,
        CONF_REFRESH_TOKEN: "renewed",
    }
    assert entry.runtime_data is coordinator
    assert coordinator.cloud.connected
    assert cloud.resumed == [REFRESH_TOKEN]

    # Nor is another account's session saved.
    renew(tokens("other", account_id="another-account"))
    assert entry.data[CONF_CLOUD][CONF_REFRESH_TOKEN] == "renewed"

    # A renewal isn't mistaken for a new sign-in, which would cut the next retry delay short.
    await cloud.send(ApiError(400))
    await settle_hass(hass)
    assert cloud.resumed == [REFRESH_TOKEN]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle()
    assert cloud.resumed == [REFRESH_TOKEN, "renewed"]


async def test_renewal_after_unlinking(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A renewal that races unlinking doesn't link the account again."""
    entry = await setup_cloud(hass)
    hass.config_entries.async_update_entry(entry, data={})
    cloud.clients[0].on_token_update(tokens("renewed"))
    assert entry.data == {}
    await settle_hass(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.cloud is None
    assert cloud.clients[0].closed


async def test_reauth(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Signing in again reconnects the cloud without reloading the entry."""
    cloud.failures["resume"] = [AuthenticationError("Rejected")]
    entry = await setup_cloud(hass)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    (flow,) = reauth_flows(hass)
    await assert_bluetooth_works(hass, entry, 30)
    # A rejected session isn't retried until the user signs in again.
    coordinator.cloud.async_entry_updated()
    await settle()
    assert len(cloud.clients) == 1

    cloud.login_tokens = tokens("second-token")
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await settle_hass(hass)
    await cloud.connect()
    assert cloud.resumed == [REFRESH_TOKEN, "second-token"]
    assert entry.runtime_data is coordinator
    assert coordinator.cloud.connected
    assert PASSWORD not in str(entry.data)


@pytest.fixture
async def service(socket_enabled) -> AsyncGenerator[FakeService]:
    """Run a local service that the real client talks to."""
    fake = FakeService()
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    root = f"http://127.0.0.1:{runner.addresses[0][1]}"
    with (
        patch("combustion_cloud.auth.IDENTITY_URL", f"{root}/identity/"),
        patch("combustion_cloud.auth.REFRESH_URL", f"{root}/token"),
        patch("combustion_cloud.client.API_URL", f"{root}/api"),
        patch("combustion_cloud.client.WS_URL", f"{root.replace('http', 'ws', 1)}/ws"),
    ):
        yield fake
        for socket in fake.sockets:
            await socket.close()
        await runner.cleanup()


async def eventually(condition) -> None:
    """Wait for something the real client does."""
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


async def test_real_client(hass: HomeAssistant, service: FakeService) -> None:
    """The published client connects through Home Assistant's shared session."""
    entry = await setup_entry(hass, data=CLOUD_DATA)
    manager = entry.runtime_data.cloud
    await eventually(lambda: manager.snapshot is not None)
    assert manager.connected
    assert core_temperature(manager) == 20
    assert entry.data[CONF_CLOUD][CONF_REFRESH_TOKEN] == f"renewed-{REFRESH_TOKEN}"

    await service.sockets[0].send_json(
        {"type": "probe_status_update", "data": status_data(temperature=55.0)}
    )
    await eventually(lambda: core_temperature(manager) == 55)
    await assert_bluetooth_works(hass, entry, 30)
    assert entry.runtime_data.cloud is manager

    assert await hass.config_entries.async_unload(entry.entry_id)
    await eventually(lambda: service.sockets[0].closed)


async def test_real_client_reauth(hass: HomeAssistant, service: FakeService) -> None:
    """A revoked session asks the user to sign in, then resumes with the new one."""
    service.rejected_token = REFRESH_TOKEN
    entry = await setup_entry(hass, data=CLOUD_DATA)
    await eventually(lambda: reauth_flows(hass))
    await assert_bluetooth_works(hass, entry, 30)
    assert service.token_requests == [REFRESH_TOKEN]

    (flow,) = reauth_flows(hass)
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["reason"] == "reauth_successful"
    manager = entry.runtime_data.cloud
    await eventually(lambda: manager.connected)
    assert service.token_requests == [REFRESH_TOKEN, "second-token"]
    assert await hass.config_entries.async_unload(entry.entry_id)
