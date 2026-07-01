from unittest.mock import MagicMock

import pytest

from crt.chromecast_mgr import ChromecastManager


def _make_manager(current_time):
    """Build a ChromecastManager with the cast machinery mocked out."""
    mgr = ChromecastManager.__new__(ChromecastManager)  # skip __init__
    mgr.cast = MagicMock()
    mgr.cast.media_controller = MagicMock()
    mgr.current_time = current_time
    return mgr


def test_seek_relative_forward_calls_seek_with_sum():
    mgr = _make_manager(current_time=10.0)
    mgr.seek_relative(30)
    mgr.cast.media_controller.seek.assert_called_once_with(40.0)


def test_seek_relative_backward_calls_seek_with_difference():
    mgr = _make_manager(current_time=60.0)
    mgr.seek_relative(-15)
    mgr.cast.media_controller.seek.assert_called_once_with(45.0)


def test_seek_relative_backward_clamps_to_zero():
    mgr = _make_manager(current_time=5.0)
    mgr.seek_relative(-15)
    mgr.cast.media_controller.seek.assert_called_once_with(0.0)


def test_seek_relative_with_none_current_time_is_noop():
    mgr = _make_manager(current_time=None)
    mgr.seek_relative(30)
    mgr.cast.media_controller.seek.assert_not_called()


def test_seek_relative_updates_cached_current_time():
    """Cache is updated optimistically so back-to-back seeks stack correctly."""
    mgr = _make_manager(current_time=10.0)
    mgr.seek_relative(30)
    assert mgr.current_time == 40.0


def test_seek_relative_with_no_cast_is_noop():
    """When cast hasn't been discovered yet, seek_relative is a silent no-op."""
    mgr = ChromecastManager.__new__(ChromecastManager)
    mgr.cast = None
    mgr.current_time = 10.0
    mgr.seek_relative(30)  # should not raise
    # nothing else to assert — we just verify no AttributeError on None.cast


# ─── seek_relative return value + stale-current_time refresh (issue #7) ───


def _make_manager_with_status(cached_time, status):
    """Manager whose media_controller.status is a controllable stand-in."""
    mgr = ChromecastManager.__new__(ChromecastManager)
    mgr.cast = MagicMock()
    mgr.cast.media_controller = MagicMock()
    mgr.cast.media_controller.status = status
    mgr.current_time = cached_time
    mgr.duration = 0.0
    return mgr


def test_seek_relative_returns_true_when_seek_issued():
    mgr = _make_manager(current_time=10.0)
    assert mgr.seek_relative(30) is True


def test_seek_relative_returns_false_with_no_cast():
    mgr = ChromecastManager.__new__(ChromecastManager)
    mgr.cast = None
    mgr.current_time = 10.0
    assert mgr.seek_relative(30) is False


def test_seek_relative_returns_false_with_none_current_time():
    mgr = _make_manager(current_time=None)
    assert mgr.seek_relative(30) is False


def test_seek_relative_uses_live_adjusted_time_when_playing():
    """Drift fix (issue #12): the receiver does not push a MEDIA_STATUS
    message every second, so during a long steady PLAYING stretch the
    cached current_time can be stale by minutes — landing the seek well
    behind the real position. `adjusted_current_time` extrapolates the
    real position from elapsed wall-clock time and must be preferred over
    the stale cached value (1232.5 here, standing in for "minutes old")."""
    status = MagicMock()
    status.player_state = "PLAYING"
    status.adjusted_current_time = 0.0
    mgr = _make_manager_with_status(cached_time=1232.5, status=status)

    mgr.seek_relative(30)

    mgr.cast.media_controller.update_status.assert_not_called()
    mgr.cast.media_controller.seek.assert_called_once_with(30.0)
    assert mgr.current_time == 30.0


def test_seek_relative_ignores_live_status_in_non_playback_state():
    """A live status read in UNKNOWN/IDLE must NOT clobber the preserved
    pause position (same rationale as _on_media_status)."""
    status = MagicMock()
    status.player_state = "UNKNOWN"
    status.adjusted_current_time = 0.0
    mgr = _make_manager_with_status(cached_time=60.0, status=status)

    mgr.seek_relative(-15)

    # Preserved 60.0 used, not the bogus 0.0 from the torn-down session.
    mgr.cast.media_controller.seek.assert_called_once_with(45.0)


def test_seek_relative_falls_back_to_cached_time_with_no_live_status():
    """When media_controller.status is None (no status ever received), fall
    back to the cached current_time instead of crashing."""
    mgr = _make_manager_with_status(cached_time=20.0, status=None)

    mgr.seek_relative(10)

    mgr.cast.media_controller.seek.assert_called_once_with(30.0)


# ─── current_time capture uses adjusted_current_time (issue #12) ───


def _status(player_state, adjusted_current_time=None, duration=0.0, volume_level=None):
    status = MagicMock()
    status.player_state = player_state
    status.adjusted_current_time = adjusted_current_time
    status.duration = duration
    status.volume_level = volume_level
    status.idle_reason = None
    return status


def _bare_manager():
    mgr = ChromecastManager.__new__(ChromecastManager)
    mgr.cast = MagicMock()
    mgr.player_state = "UNKNOWN"
    mgr.current_time = 0.0
    mgr.duration = 0.0
    mgr.volume = 1.0
    mgr._on_status_change = None
    mgr._previous_state = "UNKNOWN"
    mgr._last_logged_app_id = "__sentinel__"
    return mgr


def test_on_media_status_stores_adjusted_current_time_when_playing():
    """The push callback must store the extrapolated position, not the raw
    per-message snapshot, so callers reading current_time between pushes
    get an accurate value instead of one stale by however long the
    receiver waited before pushing this message."""
    mgr = _bare_manager()

    mgr._on_media_status(_status("PLAYING", adjusted_current_time=37.5, duration=100.0))

    assert mgr.current_time == 37.5


def test_poll_status_stores_adjusted_current_time_when_playing():
    mgr = _bare_manager()
    mgr.cast.media_controller.status = _status(
        "PLAYING", adjusted_current_time=37.5, duration=100.0
    )

    mgr.poll_status()

    assert mgr.current_time == 37.5
