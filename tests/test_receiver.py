import asyncio
import pytest
from crt.receiver import ReceiverChannel, sse_events

MARGINS = {"top": 10, "bottom": 12, "left": 8, "right": 6}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_subscribe_sends_config_first_and_marks_connected():
    ch = ReceiverChannel(MARGINS)
    q = ch.subscribe()
    assert ch.connected
    assert drain(q) == [{"type": "config", "margins": MARGINS}]


def test_connect_and_disconnect_callbacks_fire_on_edges():
    ch = ReceiverChannel(MARGINS)
    events = []
    ch.set_callbacks(lambda: events.append("on"), lambda: events.append("off"))
    a = ch.subscribe()
    b = ch.subscribe()
    ch.unsubscribe(a)
    ch.unsubscribe(b)
    assert events == ["on", "off"]
    assert not ch.connected


def test_send_without_subscribers_is_noop():
    ReceiverChannel(MARGINS).send({"type": "play"})  # must not raise


def test_send_reaches_every_subscriber():
    ch = ReceiverChannel(MARGINS)
    a = ch.subscribe()
    b = ch.subscribe()
    drain(a)
    drain(b)
    ch.send({"type": "play"})
    assert drain(a) == [{"type": "play"}] and drain(b) == [{"type": "play"}]


def test_live_position_extrapolates_while_playing_and_clamps_to_duration():
    clock = Clock()
    ch = ReceiverChannel(MARGINS, clock=clock)
    ch.send({"type": "load", "url": "u1", "position": 30.0, "autoplay": True})
    clock.t += 5
    assert ch.live_position == pytest.approx(35.0)
    ch.handle_report({"event": "status", "position": 90.0, "duration": 100.0, "paused": False, "src": "u1"})
    clock.t += 50
    assert ch.live_position == pytest.approx(100.0)


def test_pause_freezes_live_position():
    clock = Clock()
    ch = ReceiverChannel(MARGINS, clock=clock)
    ch.send({"type": "load", "url": "u1", "position": 10.0, "autoplay": True})
    clock.t += 4
    ch.send({"type": "pause"})
    clock.t += 60
    assert ch.live_position == pytest.approx(14.0)


def test_play_restarts_extrapolation_from_frozen_position():
    clock = Clock()
    ch = ReceiverChannel(MARGINS, clock=clock)
    ch.send({"type": "load", "url": "u1", "position": 10.0, "autoplay": False})
    clock.t += 60
    assert ch.live_position == pytest.approx(10.0)
    ch.send({"type": "play"})
    clock.t += 3
    assert ch.live_position == pytest.approx(13.0)


def test_seek_sets_position():
    clock = Clock()
    ch = ReceiverChannel(MARGINS, clock=clock)
    ch.send({"type": "load", "url": "u1", "position": 10.0, "autoplay": False})
    ch.send({"type": "seek", "position": 200.0})
    assert ch.live_position == pytest.approx(200.0)


def test_live_position_none_before_anything_loaded():
    assert ReceiverChannel(MARGINS).live_position is None


def test_report_for_previous_src_is_ignored():
    clock = Clock()
    ch = ReceiverChannel(MARGINS, clock=clock)
    ch.send({"type": "load", "url": "u2", "position": 0.0, "autoplay": True})
    ch.handle_report({"event": "status", "position": 500.0, "duration": 600.0, "paused": False, "src": "u1"})
    assert ch.live_position == pytest.approx(0.0)


def test_report_before_any_load_is_accepted():
    ch = ReceiverChannel(MARGINS)
    ch.handle_report({"event": "hello", "position": 5.0, "duration": 50.0, "paused": True, "src": None})
    assert ch.live_position == pytest.approx(5.0)
    assert ch.duration == 50.0 and ch.paused is True


@pytest.mark.asyncio
async def test_late_ended_from_previous_video_does_not_fire():
    ch = ReceiverChannel(MARGINS)
    ch.send({"type": "load", "url": "u2", "position": 0.0, "autoplay": True})
    ch.handle_report({"event": "ended", "position": 600.0, "duration": 600.0, "paused": True, "src": "u1"})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ch.wait_ended(), 0.05)
    ch.handle_report({"event": "ended", "position": 600.0, "duration": 600.0, "paused": True, "src": "u2"})
    await asyncio.wait_for(ch.wait_ended(), 0.05)


@pytest.mark.asyncio
async def test_reset_ended_clears_event():
    ch = ReceiverChannel(MARGINS)
    ch.send({"type": "load", "url": "u1", "position": 0.0, "autoplay": True})
    ch.handle_report({"event": "ended", "position": 1.0, "duration": 1.0, "paused": True, "src": "u1"})
    ch.reset_ended()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ch.wait_ended(), 0.05)


@pytest.mark.asyncio
async def test_wait_connected_times_out_and_succeeds():
    ch = ReceiverChannel(MARGINS)
    assert await ch.wait_connected(0.01) is False
    ch.subscribe()
    assert await ch.wait_connected(0.01) is True


@pytest.mark.asyncio
async def test_wait_new_subscriber_ignores_existing_ones():
    ch = ReceiverChannel(MARGINS)
    ch.subscribe()  # stale page still hanging on
    seen = ch.subscriptions
    assert await ch.wait_new_subscriber(seen, 0.01) is False

    async def late():
        await asyncio.sleep(0.01)
        ch.subscribe()

    asyncio.create_task(late())
    assert await ch.wait_new_subscriber(seen, 1.0) is True


@pytest.mark.asyncio
async def test_sse_events_yields_data_and_pings_then_unsubscribes():
    ch = ReceiverChannel(MARGINS)
    gen = sse_events(ch, ping_s=0.01)
    first = await gen.__anext__()
    assert first.startswith("data: ") and '"config"' in first and first.endswith("\n\n")
    assert await gen.__anext__() == ": ping\n\n"
    ch.send({"type": "play"})
    assert await gen.__anext__() == 'data: {"type": "play"}\n\n'
    await gen.aclose()
    assert not ch.connected
