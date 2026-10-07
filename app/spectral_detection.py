"""Experimental comparison detector. Never controls recording or event counts."""
import numpy as np


class SpectralComparison:
    def __init__(self, rate, margin_db=12, reference_s=30, wake_s=.5, sleep_s=2):
        self.rate = rate
        self.margin_db = margin_db
        self.reference_s = reference_s
        self.wake_s = wake_s
        self.sleep_s = sleep_s
        self.reference = None
        self.training = []
        self.state = "LEARNING"
        self.cursor = -1
        self.previous_start = None
        self.candidate = None
        self.candidate_count = 0
        self.score_db = None
        self.training_s = 0
        self.wake_count = 0

    def observe(self, frame, frequencies, hop_s, confirmed_sleep):
        if frame['end_sample'] <= self.cursor:
            return None
        self.cursor = frame['end_sample']
        gap = self.previous_start is not None and frame['sample_index'] != self.previous_start + round(hop_s * self.rate)
        self.previous_start = frame['sample_index']
        if gap:
            self.candidate = None
            self.candidate_count = 0
            if self.reference is None:
                self.training.clear()
                self.training_s = 0
            else:
                self.state = "UNKNOWN"
        mask = (np.asarray(frequencies) >= 8) & (np.asarray(frequencies) <= min(10000, self.rate / 2))
        values = np.asarray(frame['psd_db'])[mask]
        if not len(values):
            return None
        if self.reference is None:
            if not confirmed_sleep:
                self.training.clear()
                self.training_s = 0
                return None
            self.training.append(values)
            self.training_s = len(self.training) * hop_s
            if self.training_s < self.reference_s:
                return None
            self.reference = np.maximum(np.quantile(self.training, .95, axis=0), -100)
            self.training.clear()
            self.state = "SLEEP"
            return {"kind": "fft_sleep_start", "sample_index": frame['end_sample'],
                    "current_ua": frame.get('mean_ua', 0)}
        self.score_db = float(np.median(values - self.reference))
        target = "WAKE" if self.score_db >= self.margin_db else "SLEEP" if self.score_db <= self.margin_db / 2 else None
        if target is None or target == self.state:
            self.candidate = None
            self.candidate_count = 0
            return None
        if self.candidate is None or self.candidate[0] != target:
            self.candidate = (target, frame['sample_index'], frame.get('mean_ua', 0))
            self.candidate_count = 0
        self.candidate_count += 1
        if self.candidate_count * hop_s < (self.wake_s if target == 'WAKE' else self.sleep_s):
            return None
        self.state = target
        if target == 'WAKE':
            self.wake_count += 1
        marker = {"kind": 'fft_wake_start' if target == 'WAKE' else 'fft_sleep_start',
                  "sample_index": self.candidate[1], "current_ua": self.candidate[2]}
        self.candidate = None
        self.candidate_count = 0
        return marker

    def snapshot(self):
        return {"state": self.state, "score_db": self.score_db, "margin_db": self.margin_db,
                "training_s": self.training_s, "reference_s": self.reference_s,
                "wake_s": self.wake_s, "sleep_s": self.sleep_s, "wake_count": self.wake_count,
                "controls_recording": False}
