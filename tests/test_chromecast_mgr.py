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
