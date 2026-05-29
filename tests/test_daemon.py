import asyncio
from unittest.mock import MagicMock

import pytest

from crt import daemon


@pytest.mark.asyncio
async def test_periodic_save_loop_saves_repeatedly(tmp_path):
    """Issue #4: state must be persisted on an interval, not only on graceful
    shutdown, so a crash/OOM/reboot loses at most one interval of changes."""
    library = MagicMock()
    chromecast = MagicMock()
    chromecast.current_time = 12.0
    state_file = str(tmp_path / "state.json")

    task = asyncio.create_task(
        daemon.periodic_save_loop(library, chromecast, state_file, interval_s=0.01)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert library.save_state.call_count >= 2
    library.save_state.assert_called_with(state_file, playback_position=12.0)


@pytest.mark.asyncio
async def test_periodic_save_loop_survives_save_error(tmp_path):
    """A failing save must not kill the loop — the next tick retries."""
    library = MagicMock()
    library.save_state.side_effect = OSError("disk full")
    chromecast = MagicMock()
    chromecast.current_time = 0.0
    state_file = str(tmp_path / "state.json")

    task = asyncio.create_task(
        daemon.periodic_save_loop(library, chromecast, state_file, interval_s=0.01)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert library.save_state.call_count >= 2  # kept ticking despite errors


@pytest.mark.asyncio
async def test_periodic_save_loop_handles_none_current_time(tmp_path):
    """current_time may be None before the first media-status event."""
    library = MagicMock()
    chromecast = MagicMock()
    chromecast.current_time = None
    state_file = str(tmp_path / "state.json")

    task = asyncio.create_task(
        daemon.periodic_save_loop(library, chromecast, state_file, interval_s=0.01)
    )
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # None coerced to 0.0 so save_state's "ignore zero" guard applies.
    library.save_state.assert_called_with(state_file, playback_position=0.0)
