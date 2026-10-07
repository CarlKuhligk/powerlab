import asyncio
from datetime import timedelta

import numpy as np
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import OverviewPoint, SleepSegment, WakeEvent
from app.display_summary import compact_summaries
from app.main import create_app
from app.ppk.base import SampleBatch
from test_display_summary import make_active
from test_live_lod import build_manager


def append(manager, active, start, count=1000, ticks=None):
    current = np.full(count, 4.0, dtype=np.float32)
    current[count // 2] = 12345
    manager._append_preview(active, start, SampleBatch(current, np.zeros(count, dtype=np.uint8), ticks))


def stub_snapshot(manager, monkeypatch):
    monkeypatch.setattr(manager, "active_snapshot", lambda mid, **kw: {
        "measurement_id": mid, "running": mid in manager._active,
    })


def test_valid_wake_phases_arrive_after_sleep_confirmation_and_on_reconnect(tmp_path, monkeypatch):
    asyncio.run(_check_valid_wake_phases(tmp_path, monkeypatch))


async def _check_valid_wake_phases(tmp_path, monkeypatch):
    manager = build_manager(tmp_path)
    active = make_active(manager, 'wake-table', 5000)
    stub_snapshot(manager, monkeypatch)
    rate = manager.settings.sample_rate_hz
    with manager.db.session() as session:
        session.add(SleepSegment(measurement_id='wake-table', start_sample=0, end_sample=999,
                                sample_count=1000, mean_ua=4, min_ua=4, max_ua=4,
                                std_ua=0, charge_uc=4000/rate))
        session.add(WakeEvent(measurement_id='wake-table', sequence=7, start_sample=1000,
                             trigger_sample=1000, end_sample=1999, duration_us=1000/rate*1e6,
                             mean_ua=1000, peak_ua=1500, charge_uc=1000000/rate, raw_file=''))
        for kind, tick in [('sleep_start',0),('sleep_validated',100),
                           ('wake_start',1000),('wake_validated',1100),('sleep_start',2000)]:
            session.add(OverviewPoint(measurement_id='wake-table', kind=kind, sample_index=tick, current_ua=4))
    sub = manager.subscribe_live('wake-table')
    first = manager.live_frame('wake-table', sub)
    assert first['series']['valid_wake_phases'] == []
    with manager.db.session() as session:
        session.add(OverviewPoint(measurement_id='wake-table', kind='sleep_validated', sample_index=2100, current_ua=4))
    active.stream_metadata_revision += 1
    confirmed = manager.live_frame('wake-table', sub)
    assert confirmed['reset']
    phases = confirmed['series']['valid_wake_phases']
    assert len(phases) == 1 and phases[0]['sequence'] == 7
    assert phases[0]['mean_ua'] == 1000 and phases[0]['peak_ua'] == 1500
    delta = manager.live_frame('wake-table', sub)
    assert not delta['reset'] and 'valid_wake_phases' not in delta['series']
    other = manager.subscribe_live('wake-table')
    assert manager.live_frame('wake-table', other)['series']['valid_wake_phases'] == phases
    manager.unsubscribe_live('wake-table', sub)
    manager.unsubscribe_live('wake-table', other)


def test_initial_sync_then_only_new_blocks_and_independent_reconnect(tmp_path, monkeypatch):
    async def check():
        manager = build_manager(tmp_path)
        active = make_active(manager, "stream", 5000)
        stub_snapshot(manager, monkeypatch)
        append(manager, active, 0)
        sub = manager.subscribe_live("stream")
        await sub.wait()
        first = manager.live_frame("stream", sub, 500)
        assert first["reset"]
        assert first["series"]["summary_points"][0]["sample_index"] == 0
        append(manager, active, 1000)
        await sub.wait()
        delta = manager.live_frame("stream", sub, 500)
        assert not delta["reset"]
        assert delta["series"]["summary_points"][0]["sample_index"] == 1000
        assert max(p["max_ua"] for p in delta["series"]["summary_points"]) == 12345
        other = manager.subscribe_live("stream")
        await other.wait()
        resync = manager.live_frame("stream", other, 500)
        assert resync["reset"]
        assert resync["series"]["summary_points"][0]["sample_index"] == 0
        assert resync["series"]["summary_points"][-1]["end_sample"] == 1999
        manager.unsubscribe_live("stream", sub)
        manager.unsubscribe_live("stream", other)
        assert manager._live_subscribers == {}
    asyncio.run(check())


def test_idle_stream_has_no_polling_and_notifications_are_bounded(tmp_path, monkeypatch):
    async def check():
        manager = build_manager(tmp_path)
        sub = manager.subscribe_live("stream")
        await sub.wait()
        try:
            await asyncio.wait_for(sub.wait(), 0.05)
            raise AssertionError("Idle stream woke without new data")
        except TimeoutError:
            pass
        for _ in range(10_000):
            manager._notify_live("stream")
        assert sub.pending
        await sub.wait()
        assert not sub.pending
        manager.unsubscribe_live("stream", sub)
        manager._notify_live("stream")
        assert sub.closed and not sub.pending
    asyncio.run(check())


def test_slow_viewer_and_compaction_reset_preserve_weighted_blocks_and_gaps(tmp_path, monkeypatch):
    async def check():
        manager = build_manager(tmp_path)
        active = make_active(manager, "slow", 300_000)
        stub_snapshot(manager, monkeypatch)
        append(manager, active, 0, 7)
        sub = manager.subscribe_live("slow")
        await sub.wait()
        manager.live_frame("slow", sub, 500)
        append(manager, active, 7, 100_000)
        active.preview_history = compact_summaries(active.preview_history, 10)
        append(manager, active, 101_007, 100_000)
        await sub.wait()
        frame = manager.live_frame("slow", sub, 500)
        assert frame["reset"]  # Existing cursor falls inside a merged block.
        blocks = frame["series"]["summary_points"]
        assert len(blocks) <= 500
        assert sum(p["sample_count"] for p in blocks) == 200_007
        assert max(p["max_ua"] for p in blocks) == 12345
        assert any(a["end_sample"] + 1 < b["sample_index"] and a["line_key"] != b["line_key"]
                   for a, b in zip(blocks, blocks[1:]))
        manager.unsubscribe_live("slow", sub)
    asyncio.run(check())


def test_protocol_time_and_metadata_changes_reset_the_stream(tmp_path, monkeypatch):
    async def check():
        manager = build_manager(tmp_path)
        active = make_active(manager, "epoch", 10_000)
        stub_snapshot(manager, monkeypatch)
        append(manager, active, 0)
        sub = manager.subscribe_live("epoch")
        await sub.wait()
        manager.live_frame("epoch", sub)
        active.started_at += timedelta(seconds=1)
        active.preview_history = []
        append(manager, active, 0)
        await sub.wait()
        assert manager.live_frame("epoch", sub)["reset"]
        active.stream_metadata_revision += 1
        append(manager, active, 1000)
        await sub.wait()
        assert manager.live_frame("epoch", sub)["reset"]
        manager.unsubscribe_live("epoch", sub)
    asyncio.run(check())


def test_websocket_acquisition_to_delta_stop_and_cleanup(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'socket.db'}")
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        active = make_active(manager, "socket", 5000)
        stub_snapshot(manager, monkeypatch)
        append(manager, active, 0)
        with client.websocket_connect("/ws/live/socket?max_points=500") as socket:
            assert socket.receive_json()["reset"]
            append(manager, active, 1000)
            delta = socket.receive_json()
            assert delta["type"] == "live" and not delta["reset"]
            assert delta["series"]["summary_points"][0]["sample_index"] == 1000
            manager._active.pop("socket")
            manager._notify_live("socket")
            assert not socket.receive_json()["snapshot"]["running"]
        assert manager._live_subscribers == {}
        with client.websocket_connect("/ws/live/unknown") as socket:
            assert socket.receive_json()["snapshot"] == {"measurement_id": "unknown", "running": False}
        assert manager._live_subscribers == {}


def test_overview_is_pushed_on_measurement_changes(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'overview.db'}")
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        calls = []
        monkeypatch.setattr(manager, "live_overview", lambda: calls.append(1) or {"running": [], "scheduled": []})
        with client.websocket_connect("/ws/live") as socket:
            assert socket.receive_json() == {"running": [], "scheduled": []}
            manager._notify_live("new-measurement")
            assert socket.receive_json() == {"running": [], "scheduled": []}
        assert len(calls) == 2
        assert manager._live_subscribers == {}
