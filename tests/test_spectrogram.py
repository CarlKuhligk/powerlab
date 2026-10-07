import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.spectrogram import LiveSpectrogram
from app.ppk.base import SampleBatch
from test_display_summary import make_active
from test_live_lod import build_manager
from test_live_stream import stub_snapshot


def test_tone_frequency_power_and_dc_rejection_across_usb_batches():
    rate = 100_000
    analyzer = LiveSpectrogram(rate)
    ticks = np.arange(rate)
    current = 4000 + 10 * np.sin(2 * np.pi * 40 * ticks / rate)
    for a in range(0, rate, 777):
        analyzer.append(ticks[a:a + 777], current[a:a + 777])
    frames = analyzer.snapshot()["frames"]
    assert len(frames) == 7
    spectrum = 10 ** (np.asarray(frames[-1]["psd_db"]) / 10)
    assert analyzer.frequencies[np.argmax(spectrum)] == pytest.approx(40, abs=4)
    power = np.sum(spectrum * np.diff(analyzer.edges) * rate / analyzer.size)
    assert power == pytest.approx(50, rel=.01)
    assert len(analyzer.pending) < analyzer.size
    constant = LiveSpectrogram(rate)
    constant.append(ticks[:25000], np.full(25000, 4000.0))
    assert np.max(constant.frames[0]["psd_db"]) == -200


def test_gap_discards_partial_window_and_retention_is_bounded():
    analyzer = LiveSpectrogram(1000)
    analyzer.append(np.arange(200), np.ones(200))
    analyzer.append(np.arange(1000, 1250), np.ones(250))
    assert [f["sample_index"] for f in analyzer.frames] == [1000]
    analyzer.append(np.arange(1250, 130000), np.ones(128750))
    assert len(analyzer.frames) <= 960
    assert analyzer.frames[-1]["t_s"] - analyzer.frames[0]["t_s"] <= 120
    analyzer.append(np.arange(400000, 400250), np.ones(250))
    assert len(analyzer.frames) == 1


def test_live_spectral_deltas_reconnect_and_slow_viewer_reset(tmp_path, monkeypatch):
    async def check():
        manager = build_manager(tmp_path)
        active = make_active(manager, "spectral", 100000)
        stub_snapshot(manager, monkeypatch)
        def append(start, count):
            manager._append_preview(active, start, SampleBatch(np.ones(count), np.zeros(count, dtype=np.uint8)))
        append(0, 25000)
        sub = manager.subscribe_live("spectral")
        first = manager.live_frame("spectral", sub)
        assert first["series"]["spectrogram"]["reset"]
        assert len(first["series"]["spectrogram"]["frames"]) == 1
        append(25000, 12500)
        delta = manager.live_frame("spectral", sub)
        assert not delta["series"]["spectrogram"]["reset"]
        assert [f["sample_index"] for f in delta["series"]["spectrogram"]["frames"]] == [12500]
        assert manager.live_frame("spectral", sub)["series"]["spectrogram"]["frames"] == []
        other = manager.subscribe_live("spectral")
        assert len(manager.live_frame("spectral", other)["series"]["spectrogram"]["frames"]) == 2
        # Acquisition gap ages out the entire spectral history without resetting
        # the ordinary summary stream; the spectral cursor resets independently.
        append(20000000, 25000)
        slow = manager.live_frame("spectral", sub)
        assert not slow["reset"]
        assert slow["series"]["spectrogram"]["reset"]
        assert len(slow["series"]["spectrogram"]["frames"]) == 1
        manager.unsubscribe_live("spectral", sub)
        manager.unsubscribe_live("spectral", other)
    asyncio.run(check())


def test_spectral_frames_through_real_websocket_and_stop(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'spectral.db'}")
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        active = make_active(manager, "spectral-socket", 100000)
        stub_snapshot(manager, monkeypatch)
        def append(start, count):
            ticks = np.arange(start, start + count)
            current = 4 + np.sin(2 * np.pi * 40 * ticks / settings.sample_rate_hz)
            manager._append_preview(active, start, SampleBatch(current, np.zeros(count, dtype=np.uint8), ticks))
        append(0, 25000)
        with client.websocket_connect("/ws/live/spectral-socket") as socket:
            initial = socket.receive_json()["series"]["spectrogram"]
            assert initial["reset"] and len(initial["frames"]) == 1
            append(25000, 12500)
            delta = socket.receive_json()["series"]["spectrogram"]
            assert not delta["reset"] and len(delta["frames"]) == 1
            frame = delta["frames"][0]
            assert initial["frequencies_hz"][np.argmax(frame["psd_db"])] == pytest.approx(40, abs=4)
            manager._active.pop("spectral-socket")
            manager._notify_live("spectral-socket")
            assert socket.receive_json()["series"] is None
        assert manager._live_subscribers == {}
