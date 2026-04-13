from unittest.mock import AsyncMock, MagicMock

import pytest

from queue_manager import QueueManager
from ui import CRTCastApp


@pytest.fixture
def queue():
    """Real QueueManager — pure data, no I/O."""
    return QueueManager()


@pytest.fixture
def mock_pipeline():
    """Fully mocked PipelineWorker. Async entry points return immediately."""
    p = MagicMock()
    p.run_prepare = AsyncMock()
    p.run_cast = AsyncMock()
    p.wake = MagicMock()
    p.cancel_cast = MagicMock()
    p.cancel_prepare = MagicMock()
    p.set_update_callback = MagicMock()
    p.resume_position = 0.0
    return p


@pytest.fixture
def mock_chromecast():
    """Fully mocked ChromecastManager. Defaults to disconnected."""
    c = MagicMock()
    c.discover_loop = AsyncMock()
    c.connected = False
    c.device_name = ""
    c.player_state = "UNKNOWN"
    c.current_time = 0.0
    c.duration = 0.0
    c.poll_status = MagicMock()
    c.pause_or_resume = MagicMock()
    c.stop = MagicMock()
    c.seek = MagicMock()
    c.adjust_volume = MagicMock()
    c.set_status_callback = MagicMock()
    c.set_connection_callback = MagicMock()
    c.wait_for_connection = AsyncMock()
    c.quit_app = MagicMock()
    return c


@pytest.fixture
def app(queue, mock_pipeline, mock_chromecast):
    """CRTCastApp with all I/O dependencies mocked."""
    return CRTCastApp(queue, mock_pipeline, mock_chromecast)
