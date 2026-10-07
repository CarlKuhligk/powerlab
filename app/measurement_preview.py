"""Temporary acquisition and replay of the same detectors used for measurements."""
from collections import deque
import threading
import time

import numpy as np

from .display_summary import compact_summaries
from .ppk.base import SampleBatch
from .recorder import ChunkRingBuffer, RecorderSettings
from .spectral_detection import SpectralComparison
from .spectrogram import LiveSpectrogram
from .threshold import ThresholdRecorder


class PreviewEngine:
    HISTORY_S = 60

    def __init__(self, request, rate):
        self.request = request
        self.rate = rate
        self.raw = ChunkRingBuffer(round(rate * self.HISTORY_S))
        self.points = deque(maxlen=20000)
        self.segment = 0
        self.previous_tick = None
        self.revision = 0
        self.recorder = None
        self._reset_analysis()

    def _reset_analysis(self):
        self.dispose()
        self.origin = None
        self.markers = deque(maxlen=2000)
        self.spectral = LiveSpectrogram(self.rate)
        self.comparison = SpectralComparison(self.rate, self.request.spectral_margin_db)
        self.scores = deque(maxlen=480)
        self.spectral_cursor = -1
        req = self.request
        settings = RecorderSettings(sample_rate_hz=self.rate,
            sleep_threshold_ua=req.sleep_threshold_ua, wake_threshold_ua=req.wake_threshold_ua,
            sleep_min_s=req.sleep_min_s, wake_min_ms=req.wake_min_ms,
            pre_trigger_ms=req.pre_trigger_ms, post_trigger_ms=req.post_trigger_ms)
        def protocol_start(tick):
            self.origin = tick
        def marker(point):
            if point.kind in {'sleep_start', 'wake_start', 'sleep_validated', 'wake_validated'}:
                self.markers.append({'kind': point.kind, 'sample_index': point.sample_index + (self.origin or 0),
                                     'current_ua': float(point.current_ua)})
        self.recorder = ThresholdRecorder(settings, on_protocol_start=protocol_start,
            on_sleep_segment=lambda _: None, on_wake_event=lambda _: None,
            on_wake_chunk=lambda *args: None, on_overview=marker)

    def dispose(self):
        if self.recorder is not None and self.recorder._wake_candidate is not None:
            self.recorder._wake_candidate.close()
            self.recorder._wake_candidate = None

    def _analyze(self, ticks, current, digital):
        # Short, fixed analysis chunks make calibration independent of USB batching.
        step = max(1, round(self.rate * .1))
        for a in range(0, len(ticks), step):
            idx, values, bits = ticks[a:a + step], current[a:a + step], digital[a:a + step]
            self.recorder.process(SampleBatch(values, bits, idx))
            self.spectral.append(idx, values)
            if self.request.detection_mode == 'spectral_compare' and self.origin is not None:
                for frame in self.spectral.frames:
                    if frame['end_sample'] <= self.spectral_cursor:
                        continue
                    self.spectral_cursor = frame['end_sample']
                    if frame['sample_index'] < self.origin:
                        continue
                    marker = self.comparison.observe(frame, self.spectral.frequencies,
                        self.spectral.hop / self.rate, self.recorder.state == 'SLEEP')
                    if marker:
                        self.markers.append(marker)
                    if self.scores and frame['t_s'] - self.scores[-1]['t_s'] > self.spectral.hop / self.rate * 1.01:
                        self.scores.append({'t_s': (frame['t_s'] + self.scores[-1]['t_s']) / 2, 'score_db': None})
                    self.scores.append({'t_s': frame['t_s'], 'score_db': self.comparison.score_db})
            candidate = self.recorder._wake_candidate
            if candidate is not None and candidate.count > self.rate * self.HISTORY_S:
                self.recorder._discard_wake_candidate()

    def append(self, ticks, current, digital):
        self.raw.append(ticks.copy(), current.copy(), digital.copy())
        boundaries = np.r_[0, np.flatnonzero(np.diff(ticks) != 1) + 1, len(ticks)]
        factor = max(1, round(self.rate / 250))
        for first, last in zip(boundaries[:-1], boundaries[1:]):
            if first == last:
                continue
            if int(ticks[first]) != self.previous_tick:
                self.segment += 1
            for a in range(int(first), int(last), factor):
                b = min(a + factor, int(last))
                values = current[a:b]
                self.points.append({'sample_index': int(ticks[a]), 'end_sample': int(ticks[b - 1]),
                    'sample_count': b - a, 'sum_ua': float(np.sum(values, dtype=np.float64)),
                    'current_ua': float(np.mean(values)), 'min_ua': float(np.min(values)),
                    'first_ua': float(values[0]), 'last_ua': float(values[-1]),
                    'max_ua': float(np.max(values)), 'line_key': str(self.segment)})
            self.previous_tick = int(ticks[last - 1]) + 1
        self._analyze(ticks, current, digital)

    def reconfigure(self, request):
        self.request = request
        self.revision += 1
        self._reset_analysis()
        self._analyze(*self.raw.snapshot())

    def snapshot(self):
        latest = (self.previous_tick or 0) / self.rate
        start = max(0, latest - self.HISTORY_S)
        points = compact_summaries([p for p in self.points if p['end_sample'] / self.rate >= start], 1200)
        spectral = self.spectral.snapshot()
        spectral['frames'] = [f for f in spectral['frames'] if f['t_s'] >= start]
        return {'type': 'measurement_preview', 'revision': self.revision,
            'settings': self.request.model_dump(mode='json'), 'sample_rate_hz': self.rate,
            'start_s': start, 'latest_s': latest, 'state': self.recorder.state,
            'summary_points': [{**p, 't_s': p['sample_index'] / self.rate, 'end_s': p['end_sample'] / self.rate} for p in points],
            'state_markers': [{**m, 't_s': m['sample_index'] / self.rate} for m in self.markers if m['sample_index'] / self.rate >= start],
            'spectrogram': spectral, 'spectral_scores': [p for p in self.scores if p['t_s'] >= start],
            'spectral_comparison': self.comparison.snapshot() if self.request.detection_mode == 'spectral_compare' else None}


class PreviewSession:
    def __init__(self, preview_id, request, rate, driver, timeout_s):
        self.id = preview_id
        self.request = request
        self.driver = driver
        self.engine = PreviewEngine(request, rate)
        self.timeout_s = timeout_s
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._worker, name=f'preview-{preview_id[:8]}', daemon=True)
        self.subscription = None
        self.connected = False
        self.created_at = time.monotonic()
        self.running = True
        self.paused = False
        self.error = None
        self.on_expired = lambda: None

    def notify(self):
        if self.subscription:
            self.subscription.notify()

    def _worker(self):
        next_tick = 0
        last_samples = time.monotonic()
        expired = False
        try:
            self.driver.start()
            while not self.stop_event.is_set():
                if not self.connected and time.monotonic() - self.created_at > 15:
                    expired = True
                    break
                batch = self.driver.read_batch()
                if batch is None or not batch.size:
                    if time.monotonic() - last_samples > self.timeout_s:
                        raise RuntimeError('PPK2 liefert keine verwertbaren Samples.')
                    self.stop_event.wait(.01)
                    continue
                last_samples = time.monotonic()
                ticks = batch.sample_ticks if batch.sample_ticks is not None else np.arange(next_tick, next_tick + batch.size)
                next_tick = int(ticks[-1]) + 1
                with self.lock:
                    self.engine.append(ticks, batch.currents_ua, batch.digital)
                self.notify()
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
        finally:
            try:
                self.driver.close()
            except Exception as exc:
                self.error = self.error or f'PPK2 konnte nicht geschlossen werden: {exc}'
            with self.lock:
                self.engine.dispose()
            self.running = False
            self.notify()
            if expired and not self.connected:
                self.on_expired()

    def snapshot(self):
        with self.lock:
            return {**self.engine.snapshot(), 'preview_id': self.id, 'running': self.running, 'paused': self.paused, 'error': self.error}

    def reconfigure(self, request):
        with self.lock:
            if self.error or (not self.running and not self.paused):
                raise RuntimeError('Die Vorschau läuft nicht mehr.')
            if (request.port, request.meter_mode, request.voltage_mv) != (self.request.port, self.request.meter_mode, self.request.voltage_mv):
                raise RuntimeError('Nach Änderung des Geräts, Messmodus oder der Spannung die Vorschau neu starten.')
            self.engine.reconfigure(request)
            self.request = request
        self.notify()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError('PPK2-Vorschau beendet sich noch. Bitte erneut stoppen.')
        with self.lock:
            self.engine.dispose()

    def pause(self):
        self.paused = True
        self.stop()
        self.notify()
