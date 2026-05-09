from unittest.mock import patch

import pytest

import crt.config as config_module
from crt.queue_manager import QueueItem, QueueManager


def test_queue_item_creation():
    item = QueueItem(url="https://youtube.com/watch?v=abc123")
    assert item.url == "https://youtube.com/watch?v=abc123"
    assert item.status == "queued"
    assert item.progress == 0.0
    assert item.title == ""
    assert item.error is None
    assert item.filename is None
    assert len(item.id) == 36  # uuid4


def test_add_item_queue_mode():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert len(qm.items) == 1
    assert qm.items[0].url == "https://youtube.com/watch?v=1"


def test_add_item_queue_mode_appends_to_end():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1", mode="queue")
    qm.add("https://youtube.com/watch?v=2", mode="queue")
    qm.add("https://youtube.com/watch?v=3", mode="queue")
    assert qm.items[0].url == "https://youtube.com/watch?v=1"
    assert qm.items[2].url == "https://youtube.com/watch?v=3"


def test_add_item_next_mode_inserts_after_active():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item1.status = "playing"
    qm.add("https://youtube.com/watch?v=2", mode="queue")
    qm.add("https://youtube.com/watch?v=next", mode="next")
    assert qm.items[1].url == "https://youtube.com/watch?v=next"
    assert qm.items[2].url == "https://youtube.com/watch?v=2"


def test_add_item_next_mode_no_active_inserts_at_start():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1", mode="queue")
    qm.add("https://youtube.com/watch?v=next", mode="next")
    assert qm.items[0].url == "https://youtube.com/watch?v=next"


def test_add_item_now_mode_inserts_at_start():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item1.status = "playing"
    qm.add("https://youtube.com/watch?v=2", mode="queue")
    item_now = qm.add("https://youtube.com/watch?v=now", mode="now")
    assert qm.items[0].url == "https://youtube.com/watch?v=now"


def test_remove_queued_item():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert qm.remove(item.id) is True
    assert len(qm.items) == 0


def test_remove_non_queued_item_succeeds():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item.status = "downloading"
    assert qm.remove(item.id) is True
    assert len(qm.items) == 0


def test_remove_nonexistent_item_fails():
    qm = QueueManager()
    assert qm.remove("nonexistent-id") is False


def test_move_up():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1", mode="queue")
    item2 = qm.add("https://youtube.com/watch?v=2", mode="queue")
    assert qm.move(item2.id, "up") is True
    assert qm.items[0].url == "https://youtube.com/watch?v=2"


def test_move_down():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    qm.add("https://youtube.com/watch?v=2", mode="queue")
    assert qm.move(item1.id, "down") is True
    assert qm.items[1].url == "https://youtube.com/watch?v=1"


def test_move_up_at_top_fails():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert qm.move(item1.id, "up") is False


def test_move_down_at_bottom_fails():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert qm.move(item1.id, "down") is False


def test_move_non_queued_item_succeeds():
    """move() allows swapping items of any status — no status restrictions."""
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "downloading"
    item2 = qm.add("https://youtube.com/watch?v=2")
    assert qm.move(item1.id, "down") is True
    assert qm.items[1].url == "https://youtube.com/watch?v=1"


def test_move_any_status_up():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "ready"
    assert qm.move(item2.id, "up") is True
    assert qm.items[0].url == "https://youtube.com/watch?v=2"


def test_move_any_status_down():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "playing"
    qm.add("https://youtube.com/watch?v=2")
    assert qm.move(item1.id, "down") is True
    assert qm.items[1].url == "https://youtube.com/watch?v=1"


def test_can_move_middle_item():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1")
    item2 = qm.add("https://youtube.com/watch?v=2")
    qm.add("https://youtube.com/watch?v=3")
    assert qm.can_move(item2.id, "up") is True
    assert qm.can_move(item2.id, "down") is True


def test_can_move_first_item_cannot_go_up():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    qm.add("https://youtube.com/watch?v=2")
    assert qm.can_move(item1.id, "up") is False
    assert qm.can_move(item1.id, "down") is True


def test_can_move_last_item_cannot_go_down():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1")
    item2 = qm.add("https://youtube.com/watch?v=2")
    assert qm.can_move(item2.id, "up") is True
    assert qm.can_move(item2.id, "down") is False


def test_can_move_any_status():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "playing"
    item2 = qm.add("https://youtube.com/watch?v=2")
    assert qm.can_move(item1.id, "down") is True
    assert qm.can_move(item2.id, "up") is True


def test_can_move_unknown_id_returns_false():
    qm = QueueManager()
    assert qm.can_move("nonexistent", "up") is False
    assert qm.can_move("nonexistent", "down") is False


def test_next_pending_returns_first_queued():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2", mode="queue")
    assert qm.next_pending() is item2


def test_next_pending_returns_none_when_empty():
    qm = QueueManager()
    assert qm.next_pending() is None


def test_active_item():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert qm.active_item() is None
    item1.status = "downloading"
    assert qm.active_item() is item1
    item1.status = "playing"
    assert qm.active_item() is item1
    item1.status = "done"
    assert qm.active_item() is None


def test_advance_cursor_returns_next_after_playing():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "playing"
    item2 = qm.add("https://youtube.com/watch?v=2")
    assert qm.advance_cursor(loop=False) is item2


def test_advance_cursor_returns_next_after_last_done():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2")
    qm.add("https://youtube.com/watch?v=3")
    # last done = item1 (index 0), next = item2 (index 1)
    assert qm.advance_cursor(loop=False) is item2


def test_advance_cursor_uses_last_done_when_multiple():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "done"
    item3 = qm.add("https://youtube.com/watch?v=3")
    # last done = item2 (index 1), next = item3 (index 2)
    assert qm.advance_cursor(loop=False) is item3


def test_advance_cursor_playing_takes_priority_over_done():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "playing"
    item3 = qm.add("https://youtube.com/watch?v=3")
    # playing = item2 (index 1), next = item3 (index 2)
    assert qm.advance_cursor(loop=False) is item3


def test_advance_cursor_stop_mode_returns_none_at_end():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    # cursor at last item (index 0), no next → None
    assert qm.advance_cursor(loop=False) is None


def test_advance_cursor_loop_mode_wraps_to_first():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "playing"  # last item is playing
    assert qm.advance_cursor(loop=True) is item1


def test_advance_cursor_no_cursor_empty_list():
    qm = QueueManager()
    assert qm.advance_cursor(loop=False) is None
    assert qm.advance_cursor(loop=True) is None


def test_advance_cursor_no_cursor_nonempty_list():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    qm.add("https://youtube.com/watch?v=2")
    # No playing, no done — fresh playlist: return first item
    assert qm.advance_cursor(loop=False) is item1


def test_prepare_for_play_done_with_cache_becomes_ready(tmp_path):
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "done"
    fake_mp4 = tmp_path / "cached.mp4"
    fake_mp4.touch()
    item.filename = "cached.mp4"
    with patch.object(config_module, "TEMP_DIR", str(tmp_path)):
        qm.prepare_for_play(item)
    assert item.status == "ready"


def test_prepare_for_play_done_without_cache_becomes_queued():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "done"
    item.filename = "missing.mp4"
    item.progress = 42.0
    with patch.object(config_module, "TEMP_DIR", "/nonexistent/path/that/cannot/exist"):
        qm.prepare_for_play(item)
    assert item.status == "queued"
    assert item.filename is None
    assert item.progress == 0.0


def test_prepare_for_play_error_with_cache_becomes_ready(tmp_path):
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "error"
    item.error = "download failed"
    fake_mp4 = tmp_path / "cached.mp4"
    fake_mp4.touch()
    item.filename = "cached.mp4"
    with patch.object(config_module, "TEMP_DIR", str(tmp_path)):
        qm.prepare_for_play(item)
    assert item.status == "ready"
    assert item.error is None


def test_prepare_for_play_error_without_cache_becomes_queued():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "error"
    item.error = "encode failed"
    item.filename = None
    with patch.object(config_module, "TEMP_DIR", "/nonexistent/path/that/cannot/exist"):
        qm.prepare_for_play(item)
    assert item.status == "queued"
    assert item.error is None


def test_prepare_for_play_ready_is_unchanged():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "ready"
    item.filename = "cached.mp4"
    qm.prepare_for_play(item)
    assert item.status == "ready"
    assert item.filename == "cached.mp4"


def test_prepare_for_play_queued_is_unchanged():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    # status is "queued" by default
    qm.prepare_for_play(item)
    assert item.status == "queued"


def test_prepare_for_play_encoding_is_unchanged():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "encoding"
    qm.prepare_for_play(item)
    assert item.status == "encoding"


def test_first_queued_after_cursor_no_cursor_returns_first_queued():
    """Without a cursor, behaves like the old first_queued()."""
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    qm.add("https://youtube.com/watch?v=2")
    assert qm.first_queued_after_cursor() is item1


def test_first_queued_after_cursor_skips_items_before_playing():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")  # queued — before cursor
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "playing"  # cursor
    item3 = qm.add("https://youtube.com/watch?v=3")  # queued — after cursor
    assert qm.first_queued_after_cursor() is item3


def test_first_queued_after_cursor_skips_items_before_last_done():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")  # queued — before cursor
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "done"  # cursor (last done)
    item3 = qm.add("https://youtube.com/watch?v=3")  # queued — after cursor
    assert qm.first_queued_after_cursor() is item3


def test_first_queued_after_cursor_returns_none_when_nothing_after():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "playing"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "ready"  # not queued
    assert qm.first_queued_after_cursor() is None


def test_first_queued_after_cursor_uses_last_done_of_multiple():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "done"  # this is the last done
    item3 = qm.add("https://youtube.com/watch?v=3")  # queued
    assert qm.first_queued_after_cursor() is item3


def test_first_ready_returns_first_ready_item():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1")  # queued
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "ready"
    item3 = qm.add("https://youtube.com/watch?v=3")
    item3.status = "ready"
    assert qm.first_ready() is item2


def test_first_ready_returns_none_when_no_ready():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1")
    assert qm.first_ready() is None
