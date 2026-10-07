from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .ppk.base import SampleBatch


@dataclass(slots=True)
class RecorderSettings:
    sample_rate_hz: int = 100_000
    detection_mode: str = "threshold"
    sleep_threshold_ua: float = 5.0
    wake_threshold_ua: float = 10000.0
    sleep_min_s: float = 5.0
    wake_min_ms: float = 20.0
    pre_trigger_ms: float = 1000.0
    post_trigger_ms: float = 1000.0
    sleep_checkpoint_s: float = 60.0

    def __post_init__(self):
        if self.detection_mode != "threshold":
            raise ValueError("Only threshold detection is supported")

    @property
    def pre_trigger_samples(self) -> int:
        return max(0, int(self.pre_trigger_ms / 1000 * self.sample_rate_hz))

    @property
    def event_close_samples(self) -> int:
        return max(1, int(np.ceil(self.sleep_min_s * self.sample_rate_hz)))

    @property
    def checkpoint_samples(self) -> int:
        return max(1, int(self.sleep_checkpoint_s * self.sample_rate_hz))


@dataclass(slots=True)
class SleepSegmentData:
    start_sample: int
    end_sample: int
    sample_count: int
    mean_ua: float
    min_ua: float
    max_ua: float
    std_ua: float
    charge_uc: float


@dataclass(slots=True)
class WakeEventData:
    sequence: int
    start_sample: int
    trigger_sample: int
    end_sample: int
    sample_index: np.ndarray
    current_ua: np.ndarray
    digital: np.ndarray
    duration_us: float
    peak_ua: float
    mean_ua: float
    charge_uc: float
    digital_mask_seen: int
    event_kind: str = "wake"


@dataclass(slots=True)
class OverviewData:
    sample_index: int
    current_ua: float
    kind: str
    event_sequence: int | None = None


class ChunkRingBuffer:
    def __init__(self, capacity: int):
        self.capacity = max(0, capacity)
        self._chunks: deque[tuple[np.ndarray, np.ndarray, np.ndarray]] = deque()
        self._count = 0

    def clear(self) -> None:
        self._chunks.clear()
        self._count = 0

    def append(self, idx: np.ndarray, current: np.ndarray, digital: np.ndarray) -> None:
        if self.capacity <= 0 or idx.size == 0:
            return
        idx = np.asarray(idx, dtype=np.int64).copy()
        current = np.asarray(current, dtype=np.float32).copy()
        digital = np.asarray(digital, dtype=np.uint8).copy()
        self._chunks.append((idx, current, digital))
        self._count += idx.size
        self._trim()

    def _trim(self) -> None:
        while self._count > self.capacity and self._chunks:
            excess = self._count - self.capacity
            idx, current, digital = self._chunks[0]
            if excess >= idx.size:
                self._chunks.popleft()
                self._count -= idx.size
                continue
            self._chunks[0] = (idx[excess:], current[excess:], digital[excess:])
            self._count -= excess
            break

    def snapshot(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if not self._chunks:
            return (
                np.empty(0, dtype=np.int64),
                np.empty(0, dtype=np.float32),
                np.empty(0, dtype=np.uint8),
            )
        return tuple(np.concatenate(parts) for parts in zip(*self._chunks))  # type: ignore[return-value]


class RunningStats:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.start_sample: int | None = None
        self.end_sample: int | None = None
        self.count = 0
        self.sum = 0.0
        self._sum_compensation = 0.0
        self.sum_sq = 0.0
        self.min = float("inf")
        self.max = float("-inf")

    def update(self, indices: np.ndarray, values: np.ndarray) -> None:
        if values.size == 0:
            return
        indices = np.asarray(indices, dtype=np.int64)
        values64 = np.asarray(values, dtype=np.float64)
        if self.start_sample is None:
            self.start_sample = int(indices[0])
        self.end_sample = int(indices[-1])
        self.count += int(values.size)
        contribution = float(np.sum(values64)) - self._sum_compensation
        updated_sum = self.sum + contribution
        self._sum_compensation = (updated_sum - self.sum) - contribution
        self.sum = updated_sum
        self.sum_sq += float(np.dot(values64, values64))
        self.min = min(self.min, float(np.min(values64)))
        self.max = max(self.max, float(np.max(values64)))

    def as_sleep_segment(self, sample_rate_hz: int) -> SleepSegmentData | None:
        if self.count == 0 or self.start_sample is None or self.end_sample is None:
            return None
        mean = self.sum / self.count
        variance = max(0.0, self.sum_sq / self.count - mean * mean)
        return SleepSegmentData(
            start_sample=self.start_sample,
            end_sample=self.end_sample,
            sample_count=self.count,
            mean_ua=mean,
            min_ua=self.min,
            max_ua=self.max,
            std_ua=variance**0.5,
            # Charge is integrated only over samples that were actually received.
            charge_uc=self.sum / sample_rate_hz,
        )


@dataclass
class Event:
    sequence: int
    trigger: int
    start: int
    stats: RunningStats = field(default_factory=RunningStats)
    chunks: list = field(default_factory=list)
    end: int | None = None
    raw_end: int | None = None
    mask: int = 0
    peak_sample: int = 0
    baseline: float = 0.0


class RecorderBase:
    """Shared buffering and persistence for confirmed state detectors.

    This base supplies no detection algorithm. ThresholdRecorder implements the
    state transitions; future alternatives can reuse this recording machinery.
    """

    def __init__(self, settings, *, on_sleep_segment, on_wake_event, on_overview,
                 on_protocol_start=lambda _: None, on_wake_chunk=None, on_confirmed_state=None):
        self.settings = settings
        self.on_sleep_segment = on_sleep_segment
        self.on_wake_event = on_wake_event
        self.on_overview = on_overview
        self.on_protocol_start = on_protocol_start
        self.on_wake_chunk = on_wake_chunk
        self.on_confirmed_state = on_confirmed_state
        self.state = "WAITING_SLEEP"
        self.sample_index = 0
        self.protocol_start_sample = None
        self.baseline_ua = None
        self.threshold_ua = settings.wake_threshold_ua
        self.total_samples = 0
        self.detected_lost_samples = 0
        self.total_sum_ua = 0.0
        self.total_charge_uc = 0.0
        self.peak_ua = float("-inf")
        self.wake_count = 0
        self._window = max(1, round(settings.sample_rate_hz * .001))
        self._input = ChunkRingBuffer(self._window)
        self._ring = ChunkRingBuffer(settings.pre_trigger_samples)
        self._sleep = RunningStats()
        self._candidate = RunningStats()
        self._candidate_level = None
        self._candidate_recent = ChunkRingBuffer(settings.pre_trigger_samples)
        self._active = None
        self._pending = []
        self._return = []
        self._return_count = 0
        self._total = RunningStats()
        self._sequence = 0
        self._last_protocol_tick = None

    @property
    def timeline_samples(self):
        return max(0, self.sample_index - self.protocol_start_sample) if self.protocol_start_sample is not None else 0

    def detection_info(self):
        return {"mode": self.settings.detection_mode, "state": self.state,
                "protocol_start_sample": self.protocol_start_sample,
                "candidate_ua": self._candidate_level,
                "stability_s": self._candidate.count / self.settings.sample_rate_hz if self.protocol_start_sample is None else self.settings.sleep_min_s,
                "startup_excluded_s": None if self.protocol_start_sample is None else self.protocol_start_sample / self.settings.sample_rate_hz,
                "baseline_ua": self.baseline_ua, "timeline_samples": self.timeline_samples}

    @property
    def average_current_ua(self) -> float | None:
        return self.total_sum_ua / self.total_samples if self.total_samples else None

    @property
    def sleep_current_ua(self) -> float | None:
        return self.baseline_ua

    @property
    def data_coverage_pct(self) -> float:
        elapsed = self.total_samples + self.detected_lost_samples
        return (100.0 * self.total_samples / elapsed) if elapsed else 100.0

    def live_sleep_segment(self) -> SleepSegmentData | None:
        """Return a low-cost snapshot of the currently open sleep segment.

        The snapshot is display-only. It lets the live LOD endpoint bridge the
        interval between the last persisted sleep checkpoint and the current
        device tick without shipping the 100 kS/s acquisition stream.
        """
        if self.state != "SLEEP":
            return None
        return self._sleep.as_sleep_segment(self.settings.sample_rate_hz)

    def _maybe_checkpoint(self) -> None:
        if self._sleep.count >= self.settings.checkpoint_samples:
            self._flush_sleep(force=True)

    def _flush_sleep(self, *, force: bool) -> None:
        segment = self._sleep.as_sleep_segment(self.settings.sample_rate_hz)
        if segment is None:
            return
        if not force and segment.sample_count < self.settings.checkpoint_samples:
            return
        self.on_sleep_segment(segment)
        self.on_overview(OverviewData(segment.end_sample, segment.mean_ua, "sleep"))
        self._sleep.reset()

    def process(self, batch: SampleBatch):
        if not batch.size:
            return
        ticks = batch.sample_ticks if batch.sample_ticks is not None else np.arange(self.sample_index, self.sample_index + batch.size)
        self.sample_index = int(ticks[-1]) + 1
        # Count only losses inside the protocol, including a gap at the batch boundary.
        if self.protocol_start_sample is not None and batch.sample_ticks is None:
            self.detected_lost_samples += batch.detected_lost_samples
        old = self._input.snapshot()
        idx = np.concatenate((old[0], ticks))
        current = np.concatenate((old[1], batch.currents_ua))
        digital = np.concatenate((old[2], batch.digital))
        self._input.clear()
        for begin in range(0, len(idx) - self._window + 1, self._window):
            end = begin + self._window
            self._process_window(idx[begin:end], current[begin:end], digital[begin:end])
        remainder = len(idx) % self._window
        if remainder:
            self._input.append(idx[-remainder:], current[-remainder:], digital[-remainder:])

    def _sync_totals(self):
        self.total_samples = self._total.count
        self.total_sum_ua = self._total.sum
        self.total_charge_uc = self._total.sum / self.settings.sample_rate_hz
        self.peak_ua = self._total.max

    def _raw(self, event, idx, current, digital):
        if not len(idx):
            return
        event.mask |= int(np.bitwise_or.reduce(digital))
        if self.on_wake_chunk:
            self.on_wake_chunk(event.sequence, idx, current, digital)
        else:
            event.chunks.append((idx.copy(), current.copy(), digital.copy()))

    def _metrics(self, event, idx, current):
        if not len(idx):
            return
        peak = int(np.argmax(current))
        if current[peak] > event.stats.max:
            event.peak_sample = int(idx[peak])
        event.stats.update(idx, current)

    def _emit(self, event):
        if not event.stats.count:
            return
        if event.chunks:
            idx, current, digital = (np.concatenate(parts) for parts in zip(*event.chunks))
        else:
            idx, current, digital = np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32), np.empty(0, dtype=np.uint8)
        end = event.end if event.end is not None else event.stats.end_sample
        result = WakeEventData(event.sequence, event.start, event.trigger, end, idx, current, digital,
                               (end - event.trigger + 1) / self.settings.sample_rate_hz * 1e6,
                               event.stats.max, event.stats.sum / event.stats.count,
                               event.stats.sum / self.settings.sample_rate_hz, event.mask)
        self.wake_count += 1
        self.on_wake_event(result)
        prefix = "event"
        self.on_overview(OverviewData(event.trigger, event.stats.max, prefix + "_start", event.sequence))
        self.on_overview(OverviewData(event.peak_sample, event.stats.max, prefix + "_peak", event.sequence))
        self.on_overview(OverviewData(end, self.baseline_ua, prefix + "_end", event.sequence))

    def _feed_pending(self, idx, current, digital):
        for event in list(self._pending):
            take = int(np.searchsorted(idx, event.raw_end, side="right"))
            self._raw(event, idx[:take], current[:take], digital[:take])
            if idx[-1] >= event.raw_end:
                self._emit(event)
                self._pending.remove(event)

    def _process_window(self, idx, current, digital):
        if self.protocol_start_sample is None:
            self._startup(idx, current, digital)
            return
        idx = idx - self.protocol_start_sample
        absolute_last = int(idx[-1]) + self.protocol_start_sample
        first_gap = max(0, int(idx[0]) + self.protocol_start_sample - self._last_protocol_tick - 1)
        self.detected_lost_samples += first_gap + int(np.sum(np.maximum(0, np.diff(idx) - 1)))
        self._last_protocol_tick = absolute_last
        self._total.update(idx, current)
        self._sync_totals()
        self._feed_pending(idx, current, digital)
        if self.state == "SLEEP":
            self._sleep_window(idx, current, digital)
        else:
            self._active_window(idx, current, digital)

    def _active_window(self, idx, current, digital):
        event = self._active
        within = self._within_sleep(current)
        # Allow isolated outliers; no sustained elevated sub-window may confirm sleep.
        stable = np.mean(within) >= .99
        if not stable:
            outside = np.flatnonzero(~within)
            trailing = int(outside[-1]) + 1 if outside.size else 0
            if len(idx) - trailing >= max(3, round(self.settings.sample_rate_hz * .0001)):
                for part in self._return:
                    self._raw(event, *part)
                    self._metrics(event, part[0], part[1])
                self._return.clear()
                self._return_count = 0
                self._raw(event, idx[:trailing], current[:trailing], digital[:trailing])
                self._metrics(event, idx[:trailing], current[:trailing])
                idx, current, digital = idx[trailing:], current[trailing:], digital[trailing:]
                stable = True
        contiguous = np.all(np.diff(idx) == 1) and (not self._return or idx[0] == self._return[-1][0][-1] + 1)
        if stable and not contiguous and np.all(np.diff(idx) == 1):
            for part in self._return:
                self._raw(event, *part)
                self._metrics(event, part[0], part[1])
            self._return.clear()
            self._return_count = 0
            contiguous = True
        if not (stable and contiguous):
            for part in self._return:
                self._raw(event, *part)
                self._metrics(event, part[0], part[1])
            self._return.clear()
            self._return_count = 0
            self._raw(event, idx, current, digital)
            self._metrics(event, idx, current)
            return
        self._return.append((idx.copy(), current.copy(), digital.copy()))
        self._return_count += len(idx)
        if self._return_count < self.settings.event_close_samples:
            return
        start = int(self._return[0][0][0])
        event.end = start - 1
        event.raw_end = start + round(self.settings.post_trigger_ms / 1000 * self.settings.sample_rate_hz) - 1
        for part in self._return:
            take = int(np.searchsorted(part[0], event.raw_end, side="right"))
            self._raw(event, part[0][:take], part[1][:take], part[2][:take])
            self._sleep.update(part[0], part[1])
            self._ring.append(*part)
        if idx[-1] >= event.raw_end or self.on_confirmed_state:
            self._emit(event)
        else:
            self._pending.append(event)
        self._return.clear()
        self._return_count = 0
        self._active = None
        self.state = "SLEEP"

    def finish(self):
        idx, current, digital = self._input.snapshot()
        if len(idx):
            self._process_window(idx, current, digital)
            self._input.clear()
        if self.state == "SLEEP":
            self._sleep_window(np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32), np.empty(0, dtype=np.uint8), final=True)
        if self._active is not None:
            for part in self._return:
                self._raw(self._active, *part)
                self._metrics(self._active, part[0], part[1])
            self._emit(self._active)
            self._active = None
            self._return.clear()
        for event in self._pending:
            self._emit(event)
        self._pending.clear()
        self._flush_sleep(force=True)

