"""Deterministic synthetic PPK signals; never used by the production driver."""
import numpy as np

from app.ppk.base import PowerProfilerDriver, SampleBatch


class SignalGenerator:
    def __init__(self, rate=10_000, seed=17):
        self.rate = rate
        self.rng = np.random.default_rng(seed)
        self.parts = []

    def plateau(self, seconds, ua, noise=0, oscillation=0, frequency=7, square=False):
        t = np.arange(round(seconds * self.rate)) / self.rate
        wave = np.sin(2 * np.pi * frequency * t)
        if square:
            wave = np.where(wave >= 0, 1, -1)
        self.parts.append((ua + oscillation * wave + self.rng.normal(0, noise, t.size)).astype(np.float32))
        return self

    def values(self):
        return np.concatenate(self.parts)

    def ramp(self, seconds, start_ua, end_ua, noise=0):
        count = round(seconds * self.rate)
        self.parts.append((np.linspace(start_ua, end_ua, count) +
                           self.rng.normal(0, noise, count)).astype(np.float32))
        return self

    def curved_rise(self, seconds, start_ua, end_ua, power=3):
        t=np.linspace(0,1,round(seconds*self.rate))
        self.parts.append((start_ua+(end_ua-start_ua)*t**power).astype(np.float32))
        return self

    def staircase(self, steps):
        for seconds, ua in steps:
            self.plateau(seconds, ua)
        return self

    def batches(self, chunk=731, gaps=None):
        values = self.values()
        ticks = np.arange(len(values), dtype=np.int64)
        if gaps:
            for position, count in gaps:
                ticks[position:] += count
        for begin in range(0, len(values), chunk):
            end = min(begin + chunk, len(values))
            lost = sum(count for position, count in gaps or [] if begin <= position < end)
            yield SampleBatch(values[begin:end], (values[begin:end] > 100).astype(np.uint8), ticks[begin:end], lost)


class MockPPK(PowerProfilerDriver):
    def __init__(self, signal, chunk=731):
        self.sample_rate_hz = signal.rate
        self.stream = iter(signal.batches(chunk))
        self.exhausted = False

    def start(self):
        pass

    def read_batch(self):
        result = next(self.stream, None)
        if result is None:
            self.exhausted = True
        return result

    def stop(self):
        return None

    def metadata(self):
        return {"device_id": "MOCK-PPK", "sample_rate_hz": self.sample_rate_hz}
