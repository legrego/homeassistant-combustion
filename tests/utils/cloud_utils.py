"""MeatNet Cloud test utilities. All accounts, devices, and tokens are synthetic."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

from aiohttp import web
from combustion_cloud import (
    Account,
    AuthTokens,
    CloudEvent,
    CombustionCloudError,
    Connected,
    DeviceIdentity,
    DeviceStatus,
    Snapshot,
    StreamEvent,
)
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant

from custom_components.combustion.const import (
    CONF_ACCOUNT_ID,
    CONF_CLOUD,
    CONF_REFRESH_TOKEN,
)

EMAIL = "someone@example.test"
PASSWORD = "synthetic-password"
ACCOUNT_ID = "synthetic-account"
REFRESH_TOKEN = "synthetic-refresh-token"
PROBE_IDENTITY = DeviceIdentity.probe(0x10001CCC)
OTHER_IDENTITY = DeviceIdentity.probe(0x0C0FFEE1)

CLOUD_DATA = {
    CONF_CLOUD: {
        CONF_EMAIL: EMAIL,
        CONF_ACCOUNT_ID: ACCOUNT_ID,
        CONF_REFRESH_TOKEN: REFRESH_TOKEN,
    }
}


def tokens(
    refresh_token: str = REFRESH_TOKEN, *, account_id: str = ACCOUNT_ID
) -> AuthTokens:
    """Build a session."""
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    return AuthTokens(account_id, "synthetic-id-token", refresh_token, expires_at)


def status_data(
    identity: DeviceIdentity = PROBE_IDENTITY,
    *,
    age: float = 0,
    temperature: float = 20.0,
) -> dict[str, Any]:
    """Build a probe's status as the service sends it."""
    sampled_at = datetime.now(UTC) - timedelta(seconds=age)
    return {
        "device_serial_number": identity.serial_number,
        "device_type": identity.product_type,
        "owner_id": ACCOUNT_ID,
        "id": "synthetic-status",
        "collected_timestamp": sampled_at.isoformat(),
        "timestamp": (sampled_at + timedelta(seconds=1)).isoformat(),
        "firmware_version": "v2.4.0",
        "hardware_revision": "v1.1-A1",
        "sample_period": 5000,
        "temperatures": [temperature + n for n in range(8)],
        "virtual_sensors": {
            "virtual_core": "T1",
            "virtual_surface": "T4",
            "virtual_ambient": "T8",
        },
        "battery_status": "OK",
        "prediction_state": "PREDICTING",
        "prediction_mode": "TIME_TO_REMOVAL",
        "prediction_type": "REMOVAL",
        "raw_prediction_seconds": 600,
        "set_point_temperature": 60,
        "food_safe_data": {
            "mode": "SIMPLIFIED",
            "product_type": "ANY_POULTRY",
            "serving": "IMMEDIATELY",
            "completion_criteria": None,
        },
        "food_safe_status": {
            "state": "NOT_SAFE",
            "log_reduction": 0,
            "seconds_above_threshold": 0,
            "sequence_number": 1,
        },
    }


def status_event(*args, **kwargs) -> CloudEvent:
    """Build a live status update."""
    return CloudEvent.from_data(
        {"type": "probe_status_update", "data": status_data(*args, **kwargs)}
    )


def account_data(
    *identities: DeviceIdentity, skipped_associations: int = 0
) -> dict[str, Any]:
    """Build account preferences as the service sends them."""
    associations: list[Any] = [
        {"serial_number": identity.serial_number, "type": identity.product_type}
        for identity in identities
    ]
    return {"associations": associations + ["unreadable"] * skipped_associations}


def snapshot(
    *statuses: dict[str, Any],
    errors: dict[DeviceIdentity, CombustionCloudError] | None = None,
    skipped_associations: int = 0,
) -> Snapshot:
    """Build a snapshot of an account linked to these devices."""
    read = [DeviceStatus.from_data(data) for data in statuses]
    errors = errors or {}
    return Snapshot(
        Account.from_data(
            account_data(
                *(status.identity for status in read),
                *errors,
                skipped_associations=skipped_associations,
            ),
            ACCOUNT_ID,
        ),
        MappingProxyType({status.identity: status for status in read}),
        MappingProxyType(errors),
    )


class FakeCloud:
    """Stands in for the service behind every client the integration creates."""

    def __init__(self) -> None:
        """Initialize a service that accepts any credentials."""
        self.clients: list[FakeClient] = []
        self.resumed: list[str] = []
        self.logins: list[tuple[str, str]] = []
        self.events: asyncio.Queue[StreamEvent | Exception | None] = asyncio.Queue()
        """Live events. An exception ends the stream with it, and ``None`` ends it quietly."""
        self.snapshot = snapshot(status_data())
        self.snapshots = 0
        self.login_tokens = tokens()
        self.on_login: Callable[[], object] = lambda: None
        """Called while a sign-in is in progress."""
        self.failures: dict[str, list[Exception]] = {
            "login": [],
            "resume": [],
            "get_account": [],
            "get_snapshot": [],
        }
        """Errors for upcoming calls, by method."""
        self.resume_gate = asyncio.Event()
        self.resume_gate.set()

    def client(self, _session, *, on_token_update=None) -> FakeClient:
        """Create a client, as the integration does."""
        client = FakeClient(self, on_token_update)
        self.clients.append(client)
        return client

    def fail(self, method: str) -> None:
        """Raise the next error queued for a method."""
        if self.failures[method]:
            raise self.failures[method].pop(0)

    async def send(self, *events: StreamEvent | Exception | None) -> None:
        """Deliver events on the live stream."""
        for event in events:
            self.events.put_nowait(event)
        await settle()

    async def connect(self) -> None:
        """Connect the live stream."""
        await self.send(Connected(datetime.now(UTC)))


class FakeClient:
    """Stands in for CombustionCloudClient."""

    def __init__(self, cloud: FakeCloud, on_token_update) -> None:
        """Initialize."""
        self.cloud = cloud
        self.on_token_update = on_token_update
        self.closed = False

    async def login(self, email: str, password: str) -> AuthTokens:
        """Sign in."""
        self.cloud.logins.append((email, password))
        self.cloud.on_login()
        self.cloud.fail("login")
        return self.cloud.login_tokens

    async def resume(self, refresh_token: str) -> AuthTokens:
        """Restore a session."""
        self.cloud.resumed.append(refresh_token)
        await self.cloud.resume_gate.wait()
        self.cloud.fail("resume")
        return tokens(refresh_token)

    async def get_account(self) -> Account:
        """Read the account."""
        self.cloud.fail("get_account")
        return self.cloud.snapshot.account

    async def get_snapshot(self) -> Snapshot:
        """Read every linked device."""
        self.cloud.snapshots += 1
        self.cloud.fail("get_snapshot")
        return self.cloud.snapshot

    async def events(self) -> AsyncGenerator[StreamEvent]:
        """Follow the live stream."""
        while (event := await self.cloud.events.get()) is not None:
            if isinstance(event, Exception):
                raise event
            yield event

    async def close(self) -> None:
        """Close the client."""
        self.closed = True


async def settle() -> None:
    """Let the cloud connection's background task react."""
    for _ in range(20):
        await asyncio.sleep(0)


async def settle_hass(hass: HomeAssistant) -> None:
    """Let the cloud connection, and then Home Assistant, react."""
    await settle()
    await hass.async_block_till_done()
    await settle()


class FakeService:
    """A local HTTP and WebSocket service for the real client."""

    def __init__(self) -> None:
        """Initialize with one linked probe."""
        self.rejected_token: str | None = None
        self.token_requests: list[str] = []
        self.sockets: list[web.WebSocketResponse] = []
        self.live = asyncio.Event()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Answer a request."""
        if request.path.startswith("/identity/"):
            return web.json_response(
                {
                    "localId": ACCOUNT_ID,
                    "idToken": "synthetic-id-token",
                    "refreshToken": "second-token",
                    "expiresIn": "3600",
                }
            )
        if request.path == "/token":
            token = (await request.post())["refresh_token"]
            self.token_requests.append(str(token))
            if token == self.rejected_token:
                return web.json_response(
                    {"error": {"message": "TOKEN_EXPIRED"}}, status=400
                )
            return web.json_response(
                {
                    "user_id": ACCOUNT_ID,
                    "id_token": "synthetic-id-token",
                    "refresh_token": f"renewed-{token}",
                    "expires_in": "3600",
                }
            )
        if request.path == "/ws":
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            self.sockets.append(socket)
            self.live.set()
            async for _ in socket:
                pass
            return socket
        if request.path.endswith("/status"):
            return web.json_response(status_data())
        return web.json_response(account_data(PROBE_IDENTITY))
