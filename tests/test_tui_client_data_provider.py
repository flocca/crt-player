from unittest.mock import MagicMock, patch

import pytest

from tui_client.data_provider import DaemonClient


def test_fetch_library_calls_correct_endpoint():
    with patch("tui_client.data_provider.httpx.Client") as MockClient:
        instance = MockClient.return_value
        instance.get.return_value.json.return_value = {
            "cursor_video_id": "A",
            "loop_mode": False,
            "items": [],
        }
        instance.get.return_value.raise_for_status = MagicMock()

        client = DaemonClient("http://daemon:8765")
        result = client.fetch_library()

        instance.get.assert_called_with("/library/items")
        assert result["cursor_video_id"] == "A"


def test_post_control_next():
    with patch("tui_client.data_provider.httpx.Client") as MockClient:
        instance = MockClient.return_value
        instance.post.return_value.json.return_value = {"ok": True, "cursor_video_id": "B"}
        instance.post.return_value.raise_for_status = MagicMock()

        client = DaemonClient("http://daemon:8765")
        result = client.next()

        instance.post.assert_called_with("/control/next")
        assert result["cursor_video_id"] == "B"


def test_post_control_play_uses_video_id_path():
    with patch("tui_client.data_provider.httpx.Client") as MockClient:
        instance = MockClient.return_value
        instance.post.return_value.json.return_value = {"ok": True, "cursor_video_id": "ABC"}
        instance.post.return_value.raise_for_status = MagicMock()

        client = DaemonClient("http://daemon:8765")
        client.play("ABC")

        instance.post.assert_called_with("/control/play/ABC")


def test_fetch_status_calls_correct_endpoint():
    with patch("tui_client.data_provider.httpx.Client") as MockClient:
        instance = MockClient.return_value
        instance.get.return_value.json.return_value = {"youtube": {"state": "ok"}}
        instance.get.return_value.raise_for_status = MagicMock()

        client = DaemonClient("http://daemon:8765")
        result = client.fetch_status()

        instance.get.assert_called_with("/status")
        assert result["youtube"]["state"] == "ok"
