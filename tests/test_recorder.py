import numpy as np
import pytest

from app.recorder import ChunkRingBuffer, RecorderSettings, RunningStats


def test_ring_buffer_retains_exact_recent_samples_and_gaps():
    ring = ChunkRingBuffer(3)
    ring.append(np.array([1, 2, 9, 10]), np.array([4, 5, 6, 7]), np.array([0, 1, 2, 3]))
    idx, current, digital = ring.snapshot()
    np.testing.assert_array_equal(idx, [2, 9, 10])
    np.testing.assert_array_equal(current, [5, 6, 7])
    np.testing.assert_array_equal(digital, [1, 2, 3])
    ring.clear()
    assert len(ring.snapshot()[0]) == 0


def test_sleep_statistics_integrate_received_samples_without_filling_gaps():
    stats = RunningStats()
    stats.update(np.array([0, 1, 5]), np.array([2, 4, 6]))
    segment = stats.as_sleep_segment(1000)
    assert segment.sample_count == 3
    assert segment.end_sample == 5
    assert segment.mean_ua == 4
    assert segment.charge_uc == pytest.approx(.012)


@pytest.mark.parametrize("mode", ["automatic", "legacy"])
def test_removed_detectors_cannot_be_constructed(mode):
    with pytest.raises(ValueError, match="Only threshold"):
        RecorderSettings(detection_mode=mode)
