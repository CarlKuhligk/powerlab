"""Bounded STFT of original current samples, independent of display summaries."""
from collections import deque

import numpy as np


class LiveSpectrogram:
    def __init__(self, rate):
        self.rate = rate
        self.size = max(8, round(rate * .25))
        self.hop = self.size // 2
        self.window = np.hanning(self.size)
        self.scale = rate * np.sum(self.window ** 2)
        # Logarithmic display bands retain individual low-frequency FFT bins.
        self.edges = np.unique(np.r_[1, np.geomspace(1, self.size // 2 + 1, 129).astype(int)])
        self.frequencies = ((self.edges[:-1] + self.edges[1:] - 1) / 2 * rate / self.size).tolist()
        self.frames = deque(maxlen=max(1, int(120 * rate / self.hop)))
        self.pending = np.empty(0)
        self.start = None
        self.next_sample = None

    def append(self, indices, current):
        boundaries = np.r_[0, np.flatnonzero(np.diff(indices) != 1) + 1, len(indices)]
        for a, b in zip(boundaries[:-1], boundaries[1:]):
            if a == b:
                continue
            start = int(indices[a])
            if start != self.next_sample:
                self.pending = np.empty(0)
                self.start = start
            self.pending = np.concatenate((self.pending, current[a:b]))
            self.next_sample = int(indices[b - 1]) + 1
            offset = 0
            while len(self.pending) - offset >= self.size:
                values = self.pending[offset:offset + self.size]
                spectrum = np.abs(np.fft.rfft((values - values.mean()) * self.window)) ** 2 / self.scale
                spectrum[1:] *= 2
                if self.size % 2 == 0:
                    spectrum[-1] /= 2
                bands = np.add.reduceat(spectrum[1:], self.edges[:-1] - 1) / np.diff(self.edges)
                self.frames.append({"sample_index": self.start + offset,
                                    "mean_ua": float(values.mean()),
                                    "end_sample": self.start + offset + self.size - 1,
                                    "t_s": (self.start + offset + (self.size - 1) / 2) / self.rate,
                                    "psd_db": (10 * np.log10(np.maximum(bands, 1e-20))).tolist()})
                while self.frames and self.frames[0]["t_s"] < self.frames[-1]["t_s"] - 120:
                    self.frames.popleft()
                offset += self.hop
            self.pending = self.pending[offset:].copy()
            self.start += offset

    def snapshot(self):
        return {"frequencies_hz": self.frequencies, "window_s": self.size / self.rate,
                "hop_s": self.hop / self.rate, "history_s": 120,
                "frames": list(self.frames)}
