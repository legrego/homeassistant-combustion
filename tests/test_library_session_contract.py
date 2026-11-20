"""Review the upstream session's timeout contract at the Home Assistant boundary."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from combustion_ble import MeatNet, ProbeSession

from tests.utils.bt_utils import PROBE_ADDRESS, PROBE_SERIAL, probe_payload


@pytest.mark.xfail(
    strict=True,
    reason="combustion_ble PR #48: cancelled start waits indefinitely on stalled unsubscribe cleanup",
)
async def test_subscription_timeout_propagates_when_cleanup_stalls():
    """The host's subscription deadline must remain effective when GATT cleanup stalls."""
    meatnet = MeatNet()
    meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload())
    client = Mock(is_connected=True)

    async def stalled(*args):
        await asyncio.Event().wait()

    client.start_notify = AsyncMock(side_effect=stalled)
    client.stop_notify = AsyncMock(side_effect=stalled)
    session = ProbeSession(meatnet, PROBE_SERIAL, client)

    async def start_with_deadline():
        async with asyncio.timeout(0.01):
            await session.start()

    task = asyncio.create_task(start_with_deadline())
    try:
        done, _ = await asyncio.wait({task}, timeout=0.1)
        assert done, (
            "start() remained pending for 10 times the host's subscription deadline"
        )
        with pytest.raises(TimeoutError):
            await task
    finally:
        # A second cancellation interrupts the library's unbounded failure cleanup.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        client.is_connected = False
        await session.stop()
