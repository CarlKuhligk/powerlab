"""Fixed confirmations with a learned sleep reference for retrospective onset."""
import numpy as np

from .recorder import RecorderBase, Event, ChunkRingBuffer, OverviewData, RunningStats
from .wake_candidate import WakeCandidate
from .sleep_pattern import SleepPattern


class ThresholdRecorder(RecorderBase):
    def __init__(self, settings, **kwargs):
        super().__init__(settings, **kwargs)
        if not (0 < settings.sleep_threshold_ua < settings.wake_threshold_ua
                and np.isfinite(settings.wake_threshold_ua)
                and 0 < settings.sleep_min_s <= 60 and 0 < settings.wake_min_ms <= 60000):
            raise ValueError("Invalid fixed thresholds or confirmation durations")
        self._wake_samples = max(1, int(np.ceil(settings.wake_min_ms * settings.sample_rate_hz / 1000)))
        self._sleep_detection = ChunkRingBuffer(0)
        # Analysis history is independent of the exported pre-roll. Keep raw
        # context for both confirmations, plus the requested pre-roll; longer
        # continuous activity is additionally retained on the candidate spool.
        history = settings.pre_trigger_samples + settings.event_close_samples + self._wake_samples
        self._ring = ChunkRingBuffer(max(history, self._window * 20))
        self._candidate_recent = ChunkRingBuffer(self._ring.capacity)
        self._wake_candidate = None
        self._wake_high_count = 0
        self._wake_sleep_count = 0
        self._wake_threshold_tick = None
        self._previous_tick = None
        self.baseline_ua = settings.sleep_threshold_ua
        self._sleep_pattern = SleepPattern(settings.sample_rate_hz)
        self._wake_onset_method = None
        self._last_wake_reference = None

    def detection_info(self):
        info = super().detection_info()
        info.update(mode="threshold", required_stability_s=self.settings.sleep_min_s,
                    sleep_threshold_ua=self.settings.sleep_threshold_ua,
                    wake_threshold_ua=self.settings.wake_threshold_ua,
                    sleep_min_s=self.settings.sleep_min_s, wake_min_ms=self.settings.wake_min_ms,
                    background_enabled=False,
                    wake_candidate_samples=self._wake_candidate.count if self._wake_candidate else 0,
                    wake_confirmation_samples=self._wake_high_count)
        reference = self._sleep_pattern.snapshot()
        info['sleep_pattern'] = {k:v for k,v in reference.items() if k not in ('pulse_shape',)}
        info['wake_onset_method'] = self._wake_onset_method
        info['wake_sleep_reference'] = self._last_wake_reference
        info['analysis_buffer_samples'] = self._ring.capacity
        info['analysis_buffer_s'] = self._ring.capacity / self.settings.sample_rate_hz
        return info

    def _within_sleep(self, current):
        return current < self.settings.sleep_threshold_ua

    def _process_window(self, idx, current, digital):
        # Homogeneous runs make the shared return buffer strictly continuous:
        # no averaging, outlier allowance, or USB-batch-dependent decisions.
        below = self._within_sleep(current)
        cuts = np.flatnonzero((below[1:] != below[:-1]) | (np.diff(idx) != 1)) + 1
        for first, last in zip(np.r_[0, cuts], np.r_[cuts, len(idx)]):
            gap = self._previous_tick is not None and int(idx[first]) != self._previous_tick + 1
            if gap:
                if self.state == "SLEEP":
                    missing = int(idx[first]) - self._previous_tick - 1
                    candidate = self._wake_candidate
                    reference = self._sleep_pattern
                    # A sub-window loss inside an already observed departure
                    # must not move its onset to the far side of the gap. Keep
                    # only observed samples; never count missing ticks toward
                    # either confirmation or a learned periodic exemption.
                    keep_onset = (0 < missing < self._window and candidate is not None
                                  and reference.ready and candidate.last_current is not None
                                  and candidate.last_current > reference.upper
                                  and float(current[first]) > reference.upper)
                    if keep_onset:
                        self._wake_high_count = 0
                        self._wake_sleep_count = 0
                        self._wake_threshold_tick = None
                        reference.pulses.clear()
                    else:
                        self._discard_wake_candidate()
                        self._ring.clear()
                        self._sleep_pattern = SleepPattern(self.settings.sample_rate_hz)
                self._sleep_detection.clear()
                if self.protocol_start_sample is None:
                    self._candidate.reset()
                    self._candidate_recent.clear()
                elif self._active is not None:
                    for part in self._return:
                        self._raw(self._active, *part)
                        self._metrics(self._active, part[0], part[1])
                    self._return.clear()
                    self._return_count = 0
            super()._process_window(idx[first:last], current[first:last], digital[first:last])
            self._previous_tick = int(idx[last - 1])

    def _startup(self, idx, current, digital):
        if not np.all(self._within_sleep(current)):
            self._candidate.reset()
            self._candidate_level = None
            self._candidate_recent.clear()
            return
        if not self._candidate.count:
            self._candidate_level = float(current[0])
        needed = self.settings.event_close_samples - self._candidate.count
        take = min(len(idx), needed)
        self._candidate.update(idx[:take], current[:take])
        self._candidate_recent.append(idx[:take], current[:take], digital[:take])
        if self._candidate.count < self.settings.event_close_samples:
            return
        self.protocol_start_sample = self._candidate.start_sample
        self._last_protocol_tick = int(idx[take - 1])
        self._total = self._candidate
        self._candidate = RunningStats()
        self._sync_totals()
        self.baseline_ua = self._total.sum / self._total.count
        self._sleep.__dict__.update(self._total.__dict__)
        self._sleep.start_sample = 0
        self._sleep.end_sample -= self.protocol_start_sample
        recent = self._candidate_recent.snapshot()
        self._ring.append(recent[0] - self.protocol_start_sample, recent[1], recent[2])
        self._sleep_pattern.observe(recent[1])
        self.state = "SLEEP"
        self.on_protocol_start(self.protocol_start_sample)
        self.on_overview(OverviewData(0, self._candidate_level, "sleep_start"))
        self.on_overview(OverviewData(int(idx[take - 1]) - self.protocol_start_sample,
                                     float(current[take - 1]), "sleep_validated"))
        if self.on_confirmed_state:
            self.on_confirmed_state("sleep", 0, int(idx[take - 1]) - self.protocol_start_sample)
        if take < len(idx):
            super()._process_window(idx[take:], current[take:], digital[take:])

    def _sleep_window(self, idx, current, digital, final=False):
        if final:
            self._sleep_run(idx,current,digital,final=True)
            return
        reference = self._sleep_pattern
        resting = current <= min(reference.upper,self.settings.wake_threshold_ua) if reference.ready else self._within_sleep(current)
        cuts = np.flatnonzero(resting[1:] != resting[:-1])+1
        for first,last in zip(np.r_[0,cuts],np.r_[cuts,len(idx)]):
            if self.state == 'ACTIVE':
                self._active_window(idx[first:last],current[first:last],digital[first:last])
            else:
                self._sleep_run(idx[first:last],current[first:last],digital[first:last],resting=bool(resting[first]))

    def _sleep_run(self, idx, current, digital, final=False, resting=False):
        if final:
            self._discard_wake_candidate()
            return
        if resting:
            if self._wake_candidate is not None:
                # Isolated sub-ms ADC/range-switching dips are not a genuine
                # return to the resting plateau during a tentative wake.
                self._wake_candidate.append(idx, current, digital)
                self._wake_sleep_count += len(idx)
                self._wake_high_count = 0
                if self._wake_sleep_count >= self._window:
                    self._discard_wake_candidate(completed=True)
                return
            self._sleep_pattern.observe(current)
            if self._sleep_pattern.ready:
                self.baseline_ua = self._sleep_pattern.mean
            self._sleep.update(idx, current)
            self._ring.append(idx, current, digital)
            self._maybe_checkpoint()
            return
        if self._wake_candidate is None:
            self._wake_candidate = WakeCandidate(self._ring.snapshot(), self.baseline_ua, self._window)
        self._wake_sleep_count = 0
        above = current > self.settings.wake_threshold_ua
        cuts = np.flatnonzero(above[1:] != above[:-1]) + 1
        for first, last in zip(np.r_[0, cuts], np.r_[cuts, len(idx)]):
            if above[first]:
                if not self._wake_high_count:
                    self._wake_threshold_tick = int(idx[first])
                take = min(last - first, self._wake_samples - self._wake_high_count)
                self._wake_high_count += int(take)
            else:
                self._wake_high_count = 0
                take = last - first
            stop = first + take
            self._wake_candidate.append(idx[first:stop], current[first:stop], digital[first:stop])
            if self._wake_high_count == self._wake_samples:
                self._confirm_wake(int(idx[stop - 1]), float(current[stop - 1]))
                if stop < len(idx):
                    self._active_window(idx[stop:], current[stop:], digital[stop:])
                return

    def _discard_wake_candidate(self, completed=False):
        candidate = self._wake_candidate
        if candidate is not None:
            try:
                if completed:
                    self._sleep_pattern.observe_pulse(candidate)
                for part in candidate.chunks():
                    self._sleep.update(part[0], part[1])
                    self._ring.append(*part)
            finally:
                candidate.close()
                self._wake_candidate = None
        self._wake_high_count = 0
        self._wake_sleep_count = 0
        self._wake_threshold_tick = None

    def _confirm_wake(self, validated_tick, validated_current):
        candidate = self._wake_candidate
        reference = self._sleep_pattern.snapshot()
        self._last_wake_reference = {k:v for k,v in reference.items() if k != 'pulse_shape'}
        if reference['ready']:
            trigger = candidate.first_pattern_departure(reference,self._wake_threshold_tick)
            self._wake_onset_method = 'sleep-pattern-departure'
        else:
            trigger = candidate.first_steep_edge(self.settings.sleep_threshold_ua,
                                                 self.settings.wake_threshold_ua, self._wake_threshold_tick)
            self._wake_onset_method = 'steep-edge-fallback-insufficient-sleep'

        self._sequence += 1
        start_current = None
        try:
            for idx, current, digital in candidate.chunks():
                split = int(np.searchsorted(idx, trigger))
                self._sleep.update(idx[:split], current[:split])
                self._ring.append(idx[:split], current[:split], digital[:split])
                if split == len(idx):
                    continue
                if self._active is None:
                    self._flush_sleep(force=True)
                    pre = self._ring.snapshot()
                    # Context for retrospective analysis may exceed configured
                    # pre-roll, especially when the user explicitly chooses 0.
                    pre = tuple(a[pre[0] >= trigger - self.settings.pre_trigger_samples] for a in pre)
                    self._active = Event(self._sequence, trigger, int(pre[0][0]) if len(pre[0]) else trigger)
                    self._active.baseline = self.baseline_ua
                    self._raw(self._active, *pre)
                    start_current = float(current[split])
                self._raw(self._active, idx[split:], current[split:], digital[split:])
                self._metrics(self._active, idx[split:], current[split:])
        finally:
            candidate.close()
            self._wake_candidate = None
        self._wake_high_count = 0
        self._wake_threshold_tick = None
        self.state = "ACTIVE"
        self.on_overview(OverviewData(trigger, start_current, "wake_start", self._sequence))
        self.on_overview(OverviewData(validated_tick, validated_current, "wake_validated", self._sequence))
        if self.on_confirmed_state:
            self.on_confirmed_state("wake", trigger, validated_tick)

    def _active_window(self, idx, current, digital):
        event = self._active
        start_current = float(self._return[0][1][0]) if self._return else float(current[0])
        super()._active_window(idx, current, digital)
        if self.state == "SLEEP":
            start = event.end + 1
            self._sleep_pattern = SleepPattern(self.settings.sample_rate_hz)
            ticks,values,_ = self._ring.snapshot()
            self._sleep_pattern.observe(values[ticks>=start])
            self.on_overview(OverviewData(start, start_current, "sleep_start", event.sequence))
            self.on_overview(OverviewData(start + self.settings.event_close_samples - 1,
                                         float(current[np.searchsorted(idx, start + self.settings.event_close_samples - 1)]),
                                         "sleep_validated", event.sequence))
            if self.on_confirmed_state:
                self.on_confirmed_state("sleep", start, start + self.settings.event_close_samples - 1)
            self._maybe_checkpoint()

