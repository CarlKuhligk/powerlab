from datetime import datetime, timedelta, timezone
import queue
import time

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.db import Measurement
from app.main import create_app
from app.measurement_preview import PreviewEngine, PreviewSession
from app.ppk.base import PowerProfilerDriver, SampleBatch
from app.recorder import RecorderSettings
from app.schemas import MeasurementPreviewRequest
from app.threshold import ThresholdRecorder


def request(**kwargs):
    return MeasurementPreviewRequest(port='COM4', sleep_threshold_ua=5, wake_threshold_ua=10,
                                     sleep_min_s=.1, wake_min_ms=20, **kwargs)


def test_preview_matches_real_detector_and_replays_same_samples_after_tuning():
    req = request()
    engine = PreviewEngine(req, 1000)
    current = np.r_[np.full(300, 4), np.full(200, 20), np.full(400, 4)].astype(np.float32)
    ticks = np.arange(len(current))
    digital = np.zeros(len(current), dtype=np.uint8)
    expected = []
    origin = [0]
    def marker(point):
        if point.kind in {'sleep_start', 'wake_start', 'sleep_validated', 'wake_validated'}:
            expected.append((point.kind, point.sample_index + origin[0]))
    recorder = ThresholdRecorder(RecorderSettings(sample_rate_hz=1000,
        sleep_threshold_ua=5, wake_threshold_ua=10, sleep_min_s=.1, wake_min_ms=20),
        on_protocol_start=lambda tick: origin.__setitem__(0, tick), on_overview=marker,
        on_sleep_segment=lambda _: None, on_wake_event=lambda _: None, on_wake_chunk=lambda *args: None)
    recorder.process(SampleBatch(current, digital, ticks))
    engine.append(ticks, current, digital)
    assert [(m['kind'], m['sample_index']) for m in engine.snapshot()['state_markers']] == expected
    engine.reconfigure(req.model_copy(update={'wake_threshold_ua': 100}))
    assert not any(m['kind'] == 'wake_validated' for m in engine.snapshot()['state_markers'])
    engine.reconfigure(req)
    assert [(m['kind'], m['sample_index']) for m in engine.snapshot()['state_markers']] == expected
    np.testing.assert_array_equal(engine.raw.snapshot()[1], current)
    assert engine.snapshot()['revision'] == 2
    engine.dispose()


def test_preview_history_is_bounded_and_gaps_remain_visible():
    engine = PreviewEngine(request(), 100)
    current = np.full(7000, 4, dtype=np.float32)
    ticks = np.r_[np.arange(3500), np.arange(4000, 7500)]
    engine.append(ticks, current, np.zeros(len(ticks), dtype=np.uint8))
    assert len(engine.raw.snapshot()[0]) == 6000
    frame = engine.snapshot()
    assert frame['start_s'] == 15
    assert len(frame['summary_points']) <= 1200
    assert len({p['line_key'] for p in frame['summary_points']}) == 2
    assert all(f['t_s'] >= frame['start_s'] for f in frame['spectrogram']['frames'])
    engine.dispose()


def test_fft_threshold_replay_changes_fft_markers_without_controlling_current_detector():
    engine = PreviewEngine(request(detection_mode='spectral_compare'), 1000)
    rng = np.random.default_rng(7)
    current = np.r_[4 + rng.normal(0, .01, 32000), 4 + rng.normal(0, 1, 1000)].astype(np.float32)
    engine.append(np.arange(len(current)), current, np.zeros(len(current), dtype=np.uint8))
    frame = engine.snapshot()
    assert any(m['kind'] == 'fft_wake_start' for m in frame['state_markers'])
    assert not any(m['kind'] == 'wake_validated' for m in frame['state_markers'])
    engine.reconfigure(engine.request.model_copy(update={'spectral_margin_db': 60}))
    assert not any(m['kind'] == 'fft_wake_start' for m in engine.snapshot()['state_markers'])
    assert engine.snapshot()['spectral_scores'][-1]['score_db'] > 20
    engine.dispose()


class FakePreviewPPK(PowerProfilerDriver):
    def __init__(self):
        self.batches = queue.Queue()
        self.closed = False

    def start(self):
        pass

    def read_batch(self):
        try:
            return self.batches.get(timeout=.01)
        except queue.Empty:
            return None

    def stop(self):
        self.closed = True


def test_api_stream_pause_replay_port_reservation_cleanup_and_saved_settings(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f'sqlite:///{tmp_path / "preview.db"}', sample_rate_hz=1000)
    driver = FakePreviewPPK()
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        monkeypatch.setattr(manager, '_build_driver', lambda _: driver)
        req = request().model_dump(mode='json')
        response = client.post('/api/measurement-previews', json=req)
        assert response.status_code == 200
        preview_id = response.json()['preview_id']
        assert manager._port_is_busy('com4')
        with manager.db.session() as session:
            assert session.scalar(select(func.count()).select_from(Measurement)) == 0
        assert list(manager.raw_store.measurements_dir.iterdir()) == []
        assert client.post('/api/measurement-previews', json=req).status_code == 409
        assert client.post('/api/measurements', json=req).status_code == 409
        with client.websocket_connect(f'/ws/measurement-previews/{preview_id}') as socket:
            initial = socket.receive_json()
            assert initial['spectrogram']['reset']
            current = np.r_[np.full(300, 4), np.full(200, 20), np.full(400, 4)]
            driver.batches.put(SampleBatch(current, np.zeros(len(current), dtype=np.uint8), np.arange(len(current))))
            frame = socket.receive_json()
            assert any(m['kind'] == 'wake_validated' for m in frame['state_markers'])
            assert client.post(f'/api/measurement-previews/{preview_id}/pause').status_code == 200
            paused = socket.receive_json()
            assert paused['paused'] and not paused['running']
            assert driver.closed and not manager._port_is_busy('COM4')
            tuned = {**req, 'wake_threshold_ua': 100}
            assert client.patch(f'/api/measurement-previews/{preview_id}', json=tuned).status_code == 200
            replay = socket.receive_json()
            assert replay['revision'] == 1 and replay['spectrogram']['reset']
            assert not any(m['kind'] == 'wake_validated' for m in replay['state_markers'])
            assert client.patch(f'/api/measurement-previews/{preview_id}', json={**tuned, 'voltage_mv': 3000}).status_code == 409
            assert client.patch(f'/api/measurement-previews/{preview_id}', json={**tuned, 'sleep_threshold_ua': 200}).status_code == 422
            assert manager.get_preview(preview_id).engine.request.wake_threshold_ua == 100
        deadline = time.monotonic() + 1
        while manager._previews and time.monotonic() < deadline:
            time.sleep(.01)
        assert manager._previews == {}
        scheduled = {**tuned, 'start_mode': 'scheduled', 'scheduled_start_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        saved = client.post('/api/measurements', json=scheduled)
        assert saved.status_code == 200
        assert saved.json()['settings']['wake_threshold_ua'] == 100
        assert saved.json()['status'] == 'scheduled'


def test_disconnect_releases_running_preview_without_measurement(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f'sqlite:///{tmp_path / "disconnect.db"}')
    driver = FakePreviewPPK()
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        monkeypatch.setattr(manager, '_build_driver', lambda _: driver)
        preview_id = client.post('/api/measurement-previews', json=request().model_dump(mode='json')).json()['preview_id']
        with client.websocket_connect(f'/ws/measurement-previews/{preview_id}') as socket:
            socket.receive_json()
        deadline = time.monotonic() + 1
        while manager._previews and time.monotonic() < deadline:
            time.sleep(.01)
        assert driver.closed
        assert manager._previews == {}
        assert client.get('/api/measurements').json() == []


def test_unconnected_preview_expires_and_closes_driver():
    driver = FakePreviewPPK()
    session = PreviewSession('unconnected', request(), 1000, driver, 3)
    session.created_at = time.monotonic() - 16
    session.thread.start()
    session.thread.join(timeout=1)
    assert not session.thread.is_alive()
    assert driver.closed and not session.running
    assert session.error is None


def test_hardware_start_failure_is_reported_without_saving_measurement(tmp_path, monkeypatch):
    class BrokenPPK(FakePreviewPPK):
        def start(self):
            raise RuntimeError('USB disconnected')
    settings = Settings(data_dir=tmp_path, database_url=f'sqlite:///{tmp_path / "broken.db"}')
    driver = BrokenPPK()
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        monkeypatch.setattr(manager, '_build_driver', lambda _: driver)
        preview_id = client.post('/api/measurement-previews', json=request().model_dump(mode='json')).json()['preview_id']
        with client.websocket_connect(f'/ws/measurement-previews/{preview_id}') as socket:
            result = socket.receive_json()
            assert not result['running']
            assert 'USB disconnected' in result['error']
        deadline = time.monotonic() + 1
        while manager._previews and time.monotonic() < deadline:
            time.sleep(.01)
        assert manager._previews == {} and driver.closed
        assert not manager._port_is_busy('COM4')
        assert client.get('/api/measurements').json() == []
