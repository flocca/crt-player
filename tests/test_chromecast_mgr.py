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


def test_seek_relative_refreshes_from_live_playing_status():
    """Drift fix: after a fresh play_media the cached current_time is stale
    (1232.5 from a previous, torn-down session). A live PLAYING status reads
    the real position (0.0) so the seek lands at 0+delta, not stale+delta."""
    status = MagicMock()
    status.player_state = "PLAYING"
    status.current_time = 0.0
    mgr = _make_manager_with_status(cached_time=1232.5, status=status)

    mgr.seek_relative(30)

    mgr.cast.media_controller.update_status.assert_called_once()
    mgr.cast.media_controller.seek.assert_called_once_with(30.0)
    assert mgr.current_time == 30.0


def test_seek_relative_ignores_stale_status_in_non_playback_state():
    """A status read in UNKNOWN/IDLE must NOT clobber the preserved pause
    position (same rationale as _on_media_status)."""
    status = MagicMock()
    status.player_state = "UNKNOWN"
    status.current_time = 0.0
    mgr = _make_manager_with_status(cached_time=60.0, status=status)

    mgr.seek_relative(-15)

    # Preserved 60.0 used, not the bogus 0.0 from the torn-down session.
    mgr.cast.media_controller.seek.assert_called_once_with(45.0)
