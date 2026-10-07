"""Disk-backed tentative activity, with bounded-memory retrospective edge search."""
import tempfile
from collections import deque

import numpy as np


class WakeCandidate:
    dtype = np.dtype([("tick", "<i8"), ("current", "<f4"), ("digital", "u1")])

    def __init__(self, context, baseline, window):
        self.file = tempfile.TemporaryFile()
        self.context = context
        self.baseline = baseline
        self.window = window
        self.count = 0
        self.last_current = None

    def append(self, idx, current, digital):
        values = np.empty(len(idx), dtype=self.dtype)
        values["tick"], values["current"], values["digital"] = idx, current, digital
        self.file.write(values.tobytes())
        self.count += len(idx)
        if len(current):
            self.last_current = float(current[-1])

    def chunks(self, size=65536):
        self.file.seek(0)
        while data := self.file.read(size * self.dtype.itemsize):
            values = np.frombuffer(data, dtype=self.dtype)
            yield values["tick"], values["current"], values["digital"]

    def first_pattern_departure(self, reference, fallback):
        from .sleep_pattern import expected_upper
        required = 3
        start = None
        run = 0
        onset = None
        previous_tick = None
        for idx, current, _ in self.chunks():
            if start is None:
                start = int(idx[0])
            mask = current > expected_upper(reference, idx, start)
            # Track consecutive original samples across disk chunk boundaries.
            cuts = np.flatnonzero((mask[1:] != mask[:-1]) | (np.diff(idx) != 1))+1
            for first, last in zip(np.r_[0,cuts],np.r_[cuts,len(mask)]):
                if previous_tick is not None and int(idx[first]) != previous_tick + 1:
                    run = 0
                if mask[first]:
                    if not run:
                        onset = int(idx[first])
                    run += last-first
                    if run >= required:
                        return onset
                else:
                    run = 0
                previous_tick = int(idx[last-1])
        return fallback

    def first_steep_edge(self, sleep_threshold, wake_threshold, fallback):
        # Adjacent 1-ms medians reject individual ADC spikes. No user-facing
        # derivative knob: a material rise is 1% of the configured current span.
        before = self.context[1][-self.window:]
        level = float(np.median(before)) if len(before) else self.baseline
        noise = float(np.median(np.abs(before - level))) * 1.4826 if len(before) else 0.
        rise = max((wake_threshold - sleep_threshold) * .01, sleep_threshold * 2, noise * 6)
        # Compare multiple time scales: a 2-mA flank spread over several ms
        # must not be missed merely because every 1-ms increment is small.
        history = deque(maxlen=20)
        context_idx, context_current, _ = self.context
        for start in range(max(0, len(context_idx) - 20 * self.window), len(context_idx), self.window):
            history.append((context_idx[start:start + self.window], context_current[start:start + self.window]))
        candidate_start = None
        for idx, current, _ in self.chunks(size=self.window):
            if candidate_start is None:
                candidate_start = int(idx[0])
            next_level = float(np.median(current))
            for scale in (1, 5, 20):
                older = list(history)[-scale:]
                reference = np.concatenate([p[1] for p in older]) if older else before
                reference_level = float(np.median(reference)) if len(reference) else self.baseline
                if next_level - reference_level < rise * np.sqrt(scale):
                    continue
                # Include the preceding windows. A sharp flank at the end of
                # a median block otherwise gets timestamped one block late.
                search_idx = np.concatenate([p[0] for p in older] + [idx])
                search_current = np.concatenate([p[1] for p in older] + [current])
                reference_noise = float(np.median(np.abs(reference - reference_level))) * 1.4826 if len(reference) else 0.
                tolerance = max(reference_noise * 6, abs(reference_level) * .02, np.finfo(np.float32).eps)
                mask = search_current > max(sleep_threshold, reference_level + tolerance)
                mask &= search_idx >= candidate_start
                length = min(3, max(1, self.window // 2))
                runs = np.convolve(mask.astype(int), np.ones(length, dtype=int), mode='valid') if len(mask) >= length else []
                hits = np.flatnonzero(np.asarray(runs) == length)
                if len(hits):
                    return int(search_idx[hits[0]])
            history.append((idx, current))
            level = next_level
            noise = float(np.median(np.abs(current - level))) * 1.4826
        # No resolvable steep flank: report the qualifying threshold edge rather
        # than inventing an earlier wake onset in a slow/noisy prelude.
        return fallback

    def close(self):
        self.file.close()
