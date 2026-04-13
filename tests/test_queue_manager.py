import pytest
from queue_manager import QueueItem, QueueManager


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


def test_move_non_queued_item_fails():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item1.status = "downloading"
    item2 = qm.add("https://youtube.com/watch?v=2", mode="queue")
    assert qm.move(item1.id, "down") is False


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
