from unittest.mock import MagicMock

import pytest

from crt.chromecast_mgr import ChromecastManager

# ─── DashCast receiver page ───


def _dashcast_manager():
    mgr = ChromecastManager.__new__(ChromecastManager)
    mgr.cast = MagicMock()
    mgr._dashcast = MagicMock()
    return mgr


def test_launch_receiver_loads_url_with_force():
    mgr = _dashcast_manager()
    mgr.launch_receiver("http://10.0.0.5:8765/receiver")
    mgr._dashcast.load_url.assert_called_once_with("http://10.0.0.5:8765/receiver", force=True)


def test_launch_receiver_without_cast_raises():
    mgr = ChromecastManager()
    with pytest.raises(RuntimeError):
        mgr.launch_receiver("http://x/receiver")


def test_discover_registers_dashcast_controller(monkeypatch):
    import pychromecast
    from pychromecast.controllers.dashcast import DashCastController

    cast = MagicMock()
    monkeypatch.setattr(pychromecast, "get_listed_chromecasts", lambda friendly_names: ([cast], MagicMock()))
    mgr = ChromecastManager()
    assert mgr._discover_sync() is True
    handlers = [c.args[0] for c in cast.register_handler.call_args_list]
    assert any(isinstance(h, DashCastController) for h in handlers)


def test_receiver_app_active_tracks_app_id():
    from pychromecast.config import APP_DASHCAST

    mgr = _dashcast_manager()
    mgr.cast.app_id = APP_DASHCAST
    assert mgr.receiver_app_active is True
    mgr.cast.app_id = "CC1AD845"
    assert mgr.receiver_app_active is False
    mgr.cast = None
    assert mgr.receiver_app_active is False


def test_quit_app_calls_cast_quit_app_and_swallows_errors():
    mgr = _dashcast_manager()
    mgr.cast.quit_app.side_effect = RuntimeError("timeout")
    mgr.quit_app()  # must not raise
    mgr.cast.quit_app.assert_called_once()
    ChromecastManager().quit_app()  # no cast: no-op
