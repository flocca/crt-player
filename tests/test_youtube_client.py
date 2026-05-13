from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from crt.youtube_client import PlaylistEntry, YouTubeAuthError, YouTubeClient


def _build_item(video_id, title, position, playlist_item_id=None):
    return {
        "id": playlist_item_id or f"plitem-{video_id}",
        "snippet": {
            "title": title,
            "position": position,
            "resourceId": {"videoId": video_id},
        },
    }


def _mock_response(items, next_page_token=None):
    return {"items": items, "nextPageToken": next_page_token} if next_page_token else {"items": items}


def test_list_playlist_items_single_page():
    api_mock = MagicMock()
    api_mock.playlistItems.return_value.list.return_value.execute.return_value = _mock_response([
        _build_item("vid1", "Title 1", 0),
        _build_item("vid2", "Title 2", 1),
    ])

    client = YouTubeClient(api_service=api_mock)
    entries = client.list_playlist_items("PLxxx")

    assert entries == [
        PlaylistEntry(video_id="vid1", title="Title 1", position=0, playlist_item_id="plitem-vid1"),
        PlaylistEntry(video_id="vid2", title="Title 2", position=1, playlist_item_id="plitem-vid2"),
    ]


def test_list_playlist_items_paginates():
    api_mock = MagicMock()
    list_mock = api_mock.playlistItems.return_value.list

    list_mock.return_value.execute.side_effect = [
        _mock_response([_build_item(f"vid{i}", f"T{i}", i) for i in range(50)], next_page_token="PG2"),
        _mock_response([_build_item("vid50", "T50", 50)]),
    ]

    client = YouTubeClient(api_service=api_mock)
    entries = client.list_playlist_items("PLxxx")

    assert len(entries) == 51
    assert entries[0].video_id == "vid0"
    assert entries[0].playlist_item_id == "plitem-vid0"
    assert entries[50].video_id == "vid50"
    assert entries[50].playlist_item_id == "plitem-vid50"


def test_list_playlist_items_auth_error_raises_typed():
    api_mock = MagicMock()
    err = HttpError(
        resp=MagicMock(status=401, reason="Unauthorized"),
        content=b'{"error": {"message": "Invalid Credentials"}}',
    )
    api_mock.playlistItems.return_value.list.return_value.execute.side_effect = err

    client = YouTubeClient(api_service=api_mock)
    with pytest.raises(YouTubeAuthError):
        client.list_playlist_items("PLxxx")


def test_list_playlist_items_403_raises_auth_error():
    api_mock = MagicMock()
    err = HttpError(
        resp=MagicMock(status=403, reason="Forbidden"),
        content=b'{"error": {"message": "Quota exceeded"}}',
    )
    api_mock.playlistItems.return_value.list.return_value.execute.side_effect = err

    client = YouTubeClient(api_service=api_mock)
    with pytest.raises(YouTubeAuthError):
        client.list_playlist_items("PLxxx")


def test_list_playlist_items_500_propagates():
    api_mock = MagicMock()
    err = HttpError(
        resp=MagicMock(status=500, reason="Internal Server Error"),
        content=b'{"error": {"message": "oh no"}}',
    )
    api_mock.playlistItems.return_value.list.return_value.execute.side_effect = err

    client = YouTubeClient(api_service=api_mock)
    with pytest.raises(HttpError):
        client.list_playlist_items("PLxxx")


def test_from_token_file_missing_raises():
    with pytest.raises(YouTubeAuthError):
        YouTubeClient.from_token_file("/nonexistent/path.json", "/nonexistent/secrets.json")


def test_list_playlist_items_populates_playlist_item_id():
    api_mock = MagicMock()
    raw_item = {
        "id": "PLITEM_ID_42",
        "snippet": {
            "title": "Title 1",
            "position": 0,
            "resourceId": {"videoId": "vid1"},
        },
    }
    api_mock.playlistItems.return_value.list.return_value.execute.return_value = {"items": [raw_item]}

    client = YouTubeClient(api_service=api_mock)
    entries = client.list_playlist_items("PLxxx")

    assert entries == [
        PlaylistEntry(video_id="vid1", title="Title 1", position=0, playlist_item_id="PLITEM_ID_42"),
    ]


def test_delete_playlist_item_calls_api():
    api_mock = MagicMock()
    client = YouTubeClient(api_service=api_mock)

    client.delete_playlist_item("PLITEM_42")

    api_mock.playlistItems.return_value.delete.assert_called_once_with(id="PLITEM_42")
    api_mock.playlistItems.return_value.delete.return_value.execute.assert_called_once()


def test_delete_playlist_item_404_is_swallowed():
    api_mock = MagicMock()
    resp = MagicMock()
    resp.status = 404
    api_mock.playlistItems.return_value.delete.return_value.execute.side_effect = HttpError(
        resp=resp, content=b"not found"
    )
    client = YouTubeClient(api_service=api_mock)

    # Should not raise
    client.delete_playlist_item("PLITEM_GONE")


def test_delete_playlist_item_401_raises_auth_error():
    api_mock = MagicMock()
    resp = MagicMock()
    resp.status = 401
    api_mock.playlistItems.return_value.delete.return_value.execute.side_effect = HttpError(
        resp=resp, content=b"unauthorized"
    )
    client = YouTubeClient(api_service=api_mock)

    with pytest.raises(YouTubeAuthError):
        client.delete_playlist_item("PLITEM_X")


def test_delete_playlist_item_500_propagates():
    api_mock = MagicMock()
    resp = MagicMock()
    resp.status = 500
    api_mock.playlistItems.return_value.delete.return_value.execute.side_effect = HttpError(
        resp=resp, content=b"server error"
    )
    client = YouTubeClient(api_service=api_mock)

    with pytest.raises(HttpError):
        client.delete_playlist_item("PLITEM_X")
