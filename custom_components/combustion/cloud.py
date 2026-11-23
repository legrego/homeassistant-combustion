"""The optional MeatNet Cloud account.

Reads linked devices' status from outside Bluetooth range. Nothing here may stop the integration
from using Bluetooth: the connection runs in its own background task, and every failure is
contained in it.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing, suppress
from datetime import datetime
from typing import TYPE_CHECKING

from combustion_cloud import (
    ApiError,
    AuthenticationError,
    AuthTokens,
    CloudConnectionError,
    CloudEvent,
    CombustionCloudClient,
    CombustionCloudError,
    Connected,
    DeviceStore,
    Disconnected,
    ProtocolError,
    RateLimitError,
    Snapshot,
    StreamEvent,
)
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import CONF_ACCOUNT_ID, CONF_CLOUD, CONF_REFRESH_TOKEN, LOGGER

if TYPE_CHECKING:
    from .coordinator import CombustionConfigEntry

INITIAL_RETRY_DELAY = 5
MAX_RETRY_DELAY = 300
REFUSED_RETRY_DELAY = 900
"""Seconds before trying again after an error that retrying is unlikely to fix.

For example, the service no longer accepts this client, or a bug in handling its responses.
"""

SNAPSHOT_INTERVAL = 10
"""Minimum seconds between snapshots, however often the account's preferences change."""

PREFERENCES_EVENT = "user_preferences_update"
"""Sent when the account's linked devices may have changed."""

RETRYABLE_STATUSES = frozenset({408, 425})
"""HTTP statuses below 500, besides rate limiting, that are worth retrying soon."""


async def async_sign_in(
    hass: HomeAssistant, email: str, password: str
) -> dict[str, str]:
    """Sign in and return what to store to resume the session, which excludes the password.

    Also reads the account, so a service this client can't read is found before linking it.
    """
    client = CombustionCloudClient(async_get_clientsession(hass))
    try:
        tokens = await client.login(email, password)
        await client.get_account()
        return {
            CONF_EMAIL: email,
            CONF_ACCOUNT_ID: tokens.uid,
            CONF_REFRESH_TOKEN: tokens.refresh_token,
        }
    finally:
        await client.close()


class CloudManager:
    """Keeps the linked account's devices up to date for as long as the config entry is loaded."""

    def __init__(self, hass: HomeAssistant, entry: CombustionConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.account_id: str = entry.data[CONF_CLOUD][CONF_ACCOUNT_ID]
        self._refresh_token: str = entry.data[CONF_CLOUD][CONF_REFRESH_TOKEN]
        """The token in use, to tell this session's own renewals from a new sign-in."""
        self.store = DeviceStore()
        self.connected = False
        """Whether the live stream is connected."""
        self.last_error: str | None = None
        """Why the connection last failed. Never contains credentials or identifiers."""
        self.snapshot: Snapshot | None = None
        """The account, its linked devices, and which couldn't be read, when last read."""
        self.snapshot_at: datetime | None = None
        self._new_credentials = asyncio.Event()
        self._snapshot_wanted = asyncio.Event()
        self._retry_delay = INITIAL_RETRY_DELAY
        self._logged_level = logging.NOTSET

    @callback
    def async_start(self) -> None:
        """Start connecting. Setup doesn't wait for it, and unloading the entry stops it."""
        self.entry.async_create_background_task(
            self.hass, self._async_run(), "Combustion MeatNet Cloud"
        )

    @callback
    def async_entry_updated(self) -> None:
        """Reconnect if the user has signed in again."""
        token = self.entry.data[CONF_CLOUD][CONF_REFRESH_TOKEN]
        if token != self._refresh_token:
            self._refresh_token = token
            self._new_credentials.set()

    async def _async_run(self) -> None:
        """Keep a session running. Ends only by being cancelled."""
        while True:
            self._new_credentials.clear()
            delay: float | None = INITIAL_RETRY_DELAY
            try:
                await self._async_session()
            except AuthenticationError as err:
                self._async_failed(str(err), logging.WARNING)
                self.entry.async_start_reauth(self.hass)
                delay = None
            except CombustionCloudError as err:
                delay = self._next_delay(err)
                self._async_failed(
                    f"{type(err).__name__}: {err}",
                    logging.WARNING if delay == REFUSED_RETRY_DELAY else logging.INFO,
                )
            except Exception as err:
                LOGGER.exception("Unexpected error in the MeatNet Cloud connection")
                self._async_failed(type(err).__name__, logging.DEBUG)
                delay = REFUSED_RETRY_DELAY
            with suppress(TimeoutError):
                async with asyncio.timeout(delay):
                    await self._new_credentials.wait()

    def _next_delay(self, err: CombustionCloudError) -> float:
        """Return the seconds to wait before a new session."""
        if (
            isinstance(err, ApiError)
            and not isinstance(err, RateLimitError)
            and err.status < 500
            and err.status not in RETRYABLE_STATUSES
        ):
            return REFUSED_RETRY_DELAY
        delay = self._retry_delay
        self._retry_delay = min(delay * 2, MAX_RETRY_DELAY)
        return _respect_rate_limit(err, delay)

    async def _async_session(self) -> None:
        """Connect and follow the account until something fails."""
        self._snapshot_wanted.clear()
        client = CombustionCloudClient(
            async_get_clientsession(self.hass),
            on_token_update=self._async_save_tokens,
        )
        try:
            await client.resume(self._refresh_token)
            # The stream never waits for a snapshot, so a slow or failing one doesn't delay
            # live readings.
            tasks = [
                asyncio.create_task(self._async_stream(client)),
                asyncio.create_task(self._async_take_snapshots(client)),
            ]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
        finally:
            self.connected = False
            await client.close()

    async def _async_stream(self, client: CombustionCloudClient) -> None:
        """Follow live updates. The client reconnects by itself where it can."""
        async with aclosing(client.events()) as events:
            async for event in events:
                self._async_handle_event(event)

    @callback
    def _async_handle_event(self, event: StreamEvent) -> None:
        match event:
            case Connected():
                self._async_connected()
                # The stream doesn't begin with the devices' current status.
                self._snapshot_wanted.set()
            case Disconnected(reason=reason):
                self._async_failed(reason, logging.INFO)
            case CloudEvent(status=status) if status is not None:
                self.store.update(status)
            case CloudEvent(type=kind) if kind == PREFERENCES_EVENT:
                self._snapshot_wanted.set()

    async def _async_take_snapshots(self, client: CombustionCloudClient) -> None:
        """Read every linked device when the stream connects or the linked devices change."""
        delay = INITIAL_RETRY_DELAY
        while True:
            await self._snapshot_wanted.wait()
            self._snapshot_wanted.clear()
            try:
                snapshot = await client.get_snapshot()
            except (ApiError, CloudConnectionError, ProtocolError) as err:
                LOGGER.debug(
                    "Reading MeatNet Cloud devices failed: %s: %s",
                    type(err).__name__,
                    err,
                )
                self._snapshot_wanted.set()
                await asyncio.sleep(_respect_rate_limit(err, delay))
                delay = min(delay * 2, MAX_RETRY_DELAY)
                continue
            delay = INITIAL_RETRY_DELAY
            self._async_apply_snapshot(snapshot)
            await asyncio.sleep(SNAPSHOT_INTERVAL)

    @callback
    def _async_apply_snapshot(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.snapshot_at = dt_util.utcnow()
        for status in snapshot.statuses.values():
            self.store.update(status)
        if not snapshot.account.skipped_associations:
            # Otherwise a device's absence doesn't mean it was unlinked.
            for identity in set(self.store.devices) - set(snapshot.account.devices):
                self.store.remove(identity)

    @callback
    def _async_save_tokens(self, tokens: AuthTokens) -> None:
        """Store a renewed session, unless the account has since been unlinked or changed."""
        cloud = self.entry.data.get(CONF_CLOUD)
        if cloud is None or cloud[CONF_ACCOUNT_ID] != tokens.uid:
            return
        self._refresh_token = tokens.refresh_token
        # Renewals happen about hourly and usually keep the token. Unchanged data isn't saved.
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_CLOUD: {**cloud, CONF_REFRESH_TOKEN: tokens.refresh_token},
            },
        )

    @callback
    def _async_connected(self) -> None:
        self.connected = True
        self.last_error = None
        self._retry_delay = INITIAL_RETRY_DELAY
        if self._logged_level:
            self._logged_level = logging.NOTSET
            LOGGER.info("MeatNet Cloud is connected again")

    @callback
    def _async_failed(self, reason: str, level: int) -> None:
        """Record a failure. Until the connection recovers, only a worse one is logged again."""
        self.connected = False
        self.last_error = reason
        if level > self._logged_level:
            self._logged_level = level
            LOGGER.log(level, "MeatNet Cloud is unavailable: %s", reason)


def _respect_rate_limit(err: CombustionCloudError, delay: float) -> float:
    if isinstance(err, RateLimitError) and err.retry_after is not None:
        return max(delay, err.retry_after)
    return delay
