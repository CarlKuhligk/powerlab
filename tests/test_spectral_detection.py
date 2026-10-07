import asyncio
import json

import numpy as np
import pytest

from app.db import Measurement
from app.ppk.base import SampleBatch
from app.schemas import MeasurementStartRequest
from app.spectral_detection import SpectralComparison
from test_display_summary import make_active
from test_live_lod import build_manager
from test_live_stream import stub_snapshot


def test_reference_transients_sustained_wake_hysteresis_and_return():
    detector = SpectralComparison(1000, reference_s=.5, wake_s=.5, sleep_s=.5)
    position = 0
    def observe(level, confirmed=True):
        nonlocal position
        frame = {'sample_index': position, 'end_sample': position + 249,
                 'psd_db': [level] * 8, 'mean_ua': 4}
        position += 125
        return detector.observe(frame, [8, 16, 32, 64, 128, 256, 400, 500], .125, confirmed)
    for _ in range(6):
        assert observe(-80, False) is None
    assert detector.training_s == 0
    for _ in range(3):
        assert observe(-80) is None
    assert observe(-80)['kind'] == 'fft_sleep_start'
    assert detector.training == []
    for _ in range(3):
        assert observe(-50) is None  # brief periodic pulse
    assert observe(-80) is None
    onset = position
    for _ in range(3):
        assert observe(-50) is None
    marker = observe(-50)
    assert marker['sample_index'] == onset
    assert detector.state == 'WAKE'
    for _ in range(5):
        assert observe(-71) is None  # hysteresis
    for _ in range(3):
        assert observe(-80) is None
    assert observe(-80)['kind'] == 'fft_sleep_start'
    assert detector.snapshot()['controls_recording'] is False
    assert detector.wake_count == 1


def test_gap_resets_confirmation_and_reference_is_frozen():
    detector = SpectralComparison(1000, reference_s=.125)
    def observe(start, level):
        return detector.observe({'sample_index': start, 'end_sample': start + 249, 'psd_db': [level] * 4},
                                [8, 40, 100, 400], .125, True)
    observe(0, -80)
    reference = detector.reference.copy()
    observe(125, -50)
    observe(250, -50)
    assert observe(1000, -50) is None
    assert detector.state == 'UNKNOWN'
    observe(1125, -50)
    observe(1250, -50)
    assert observe(1375, -50)['sample_index'] == 1000
    np.testing.assert_equal(reference, detector.reference)


@pytest.mark.parametrize('margin', [0, float('nan'), float('inf'), 61])
def test_api_validates_spectral_settings(margin):
    with pytest.raises(ValueError):
        MeasurementStartRequest(name='spectral', port='COM4', detection_mode='spectral_compare', spectral_margin_db=margin)


def test_original_samples_to_persisted_fft_markers_and_live_reset(tmp_path, monkeypatch):
    async def check():
        manager = build_manager(tmp_path)
        manager.settings.sample_rate_hz = 1000
        active = make_active(manager, 'comparison', 33000)
        active.request = MeasurementStartRequest(name='comparison', port='COM4', detection_mode='spectral_compare')
        with manager.db.session() as session:
            session.get(Measurement, 'comparison').settings_json = active.request.model_dump_json()
        active.recorder.protocol_start_sample = 0
        active.recorder.state = 'SLEEP'
        stub_snapshot(manager, monkeypatch)
        rng = np.random.default_rng(3)
        def append(start, size, noise):
            manager._append_preview(active, start, SampleBatch(4 + rng.normal(0, noise, size), np.zeros(size, dtype=np.uint8)))
        append(0, 32000, .01)
        sub = manager.subscribe_live('comparison')
        baseline = manager.live_frame('comparison', sub)
        assert any(m['kind'] == 'fft_sleep_start' for m in baseline['series']['state_markers'])
        append(32000, 1000, 1)
        wake = manager.live_frame('comparison', sub)
        assert wake['reset']
        assert any(m['kind'] == 'fft_wake_start' for m in wake['series']['state_markers'])
        assert active.spectral_comparison.state == 'WAKE'
        assert active.recorder.state == 'SLEEP'  # comparison never controls recorder
        saved = manager.overview('comparison')
        assert any(m['kind'] == 'fft_wake_start' for m in saved['state_markers'])
        assert not any(p['kind'].startswith('fft_') for p in saved['points'])
        assert saved['events'] == []
        _, body = manager.export_metadata_json('comparison')
        assert any(m['kind'] == 'fft_wake_start' for m in json.loads(body)['spectral_markers'])
        manager.unsubscribe_live('comparison', sub)
    asyncio.run(check())
