"""Bounded reference for resting noise and completed recurring sleep pulses."""
from collections import deque
import numpy as np


class SleepPattern:
    def __init__(self, rate):
        self.rate = rate
        self.values = deque(maxlen=4096)
        self.pulses = deque(maxlen=8)
        self.count = 0
        self.ready = False
        self.mean = self.std = self.upper = self.low = self.high = None

    def observe(self, current):
        # Freeze a confirmed reference: a slow wake must never teach its own
        # rising baseline. Relearn only after the next confirmed return to sleep.
        if self.ready or not len(current):
            return
        self.count += len(current)
        self.values.extend(float(v) for v in current[::max(1, len(current)//4096)])
        if self.count < max(32, round(.032*self.rate)):
            return
        x = np.asarray(self.values)
        center = float(np.median(x))
        sigma = float(np.median(np.abs(x-center)))*1.4826
        # A small numerical floor avoids interpreting ADC quantization as wake.
        margin = max(6*sigma, abs(center)*.02, .001)
        core = x[np.abs(x-center)<=margin]
        self.mean, self.std = float(np.mean(core)), float(np.std(core))
        self.low, self.high = (float(v) for v in np.quantile(core,[.01,.99]))
        self.upper = max(self.high, center+margin)
        self.ready = True

    def observe_pulse(self, candidate):
        if not self.ready:
            return
        first = last = None
        for idx, current, _ in candidate.chunks():
            hits = np.flatnonzero(current > self.upper)
            if len(hits):
                if first is None:
                    first = int(idx[hits[0]])
                last = int(idx[hits[-1]])
        if first is None or last-first+1 < max(3, round(.001*self.rate)):
            return
        duration = last-first+1
        sums, counts = np.zeros(64), np.zeros(64)
        for idx, current, _ in candidate.chunks():
            valid = (idx>=first)&(idx<=last)
            bins = np.minimum(63, ((idx[valid]-first)*64//duration).astype(int))
            sums += np.bincount(bins, weights=current[valid], minlength=64)
            counts += np.bincount(bins, minlength=64)
        occupied = counts>0
        shape = np.interp(np.arange(64),np.flatnonzero(occupied),sums[occupied]/counts[occupied])
        self.pulses.append((first,duration,shape))

    def snapshot(self):
        period = None
        jitter = 0.
        pulses = list(self.pulses)[-4:]
        if len(pulses)>=3:
            intervals = np.diff([p[0] for p in pulses])
            median = float(np.median(intervals))
            lengths = np.asarray([p[1] for p in pulses])
            peaks = np.asarray([np.max(p[2]) for p in pulses])
            if (median>0 and np.max(np.abs(intervals-median))<=max(.05*median,.002*self.rate)
                    and np.max(lengths)<=np.min(lengths)*1.5+.002*self.rate
                    and np.max(peaks)<=np.min(peaks)*1.5):
                period = median
                jitter = max(.05*median,.002*self.rate)
        return {'ready':self.ready, 'mean_ua':self.mean, 'std_ua':self.std,
                'low_ua':self.low, 'high_ua':self.high, 'upper_ua':self.upper,
                'period_samples':period, 'period_s':period/self.rate if period else None,
                'sample_rate_hz':self.rate, 'phase_tolerance_samples':jitter, 'pulse_count':len(self.pulses),
                'last_pulse':pulses[-1][0] if pulses else None,
                'pulse_duration_samples':max(p[1] for p in pulses) if period else None,
                'pulse_shape':np.max([p[2] for p in pulses],axis=0) if period else None}


def expected_upper(reference, idx, start):
    upper = np.full(len(idx),reference['upper_ua'],dtype=float)
    period = reference['period_samples']
    if period is None:
        return upper
    steps = max(1,round((start-reference['last_pulse'])/period))
    expected = reference['last_pulse']+steps*period
    if abs(start-expected)>reference['phase_tolerance_samples']:
        return upper
    duration = reference['pulse_duration_samples']
    # Slightly dilate the learned shape in time and amplitude for normal jitter.
    relative = idx-start
    mask = (relative>=0)&(relative<duration+max(1,round(reference['sample_rate_hz']*.001)))
    bins = np.minimum(63,(relative[mask]*64/duration).astype(int))
    shape = reference['pulse_shape']
    padded = np.pad(shape,1,mode='edge')
    shape = np.maximum.reduce([padded[:-2],padded[1:-1],padded[2:]])
    upper[mask] = np.maximum(upper[mask],shape[bins]*1.25+6*reference['std_ua'])
    return upper
