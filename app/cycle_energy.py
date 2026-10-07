"""Energy from disjoint, fully validated Sleep -> Wake -> Sleep cycles."""
from bisect import bisect_left, bisect_right
import math
from statistics import mean, variance


PERIODS = {'day': 86400, 'week': 7 * 86400, 'month': 30 * 86400, 'year': 365 * 86400}
MAX_INTERPOLATED_PHASE_S = .010
MAX_INTERPOLATED_PHASE_FRACTION = .001


def _small_gap(missing, span, sample_rate):
    return (0 <= missing <= MAX_INTERPOLATED_PHASE_S * sample_rate
            and missing <= span * MAX_INTERPOLATED_PHASE_FRACTION)


def _phase_variability(cycles, voltage, phase):
    """Unweighted phase statistics; sample variance needs at least two phases."""
    result = {'count': len(cycles), 'variance_method': 'sample', 'ddof': 1}
    values = {
        'duration_s': [c[f'{phase}_duration_s'] for c in cycles],
        'current_ua': [c[f'{phase}_charge_uc'] / c[f'{phase}_duration_s'] for c in cycles],
        'energy_uwh': [c[f'{phase}_charge_uc'] * voltage / 3600 for c in cycles],
    }
    for key, observations in values.items():
        average = mean(observations) if observations else None
        spread = variance(observations) if len(observations) > 1 else None
        stddev = math.sqrt(spread) if spread is not None else None
        result[key] = {'mean': average, 'variance': spread, 'stddev': stddev,
                       'min': min(observations) if observations else None,
                       'max': max(observations) if observations else None,
                       'cv_pct': stddev / abs(average) * 100
                       if stddev is not None and average else None}
    return result


def project(summary, duration_s):
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError('Projection duration must be finite and positive')
    return {'duration_s': duration_s, **{
        f'{kind}_energy_uwh': summary[f'average_{kind}_power_uw'] * duration_s / 3600
        if summary[f'average_{kind}_power_uw'] is not None else None
        for kind in ('wake', 'sleep', 'combined')}}


def confirmed_cycle_energy(sample_rate, voltage_mv, events, sleep_segments, markers, custom_duration_s=None):
    times = {kind: sorted({int(p.sample_index) for p in markers if p.kind == kind})
             for kind in ('sleep_start', 'sleep_validated', 'wake_start', 'wake_validated')}
    starts = times['sleep_start']
    start_set, wake_start_set = set(starts), set(times['wake_start'])
    cycles = []
    rejected_gaps = 0
    def first_between(kind, start, end):
        values = times[kind]
        at = bisect_left(values, start)
        return values[at] if at < len(values) and values[at] < end else None

    wake_events = sorted((e for e in events if e.event_kind != 'background'), key=lambda e: e.trigger_sample)
    wake_ticks = [int(e.trigger_sample) for e in wake_events]
    background = sorted((e for e in events if e.event_kind == 'background'), key=lambda e:e.trigger_sample)
    background_ticks = [int(e.trigger_sample) for e in background]
    segments = sorted(sleep_segments, key=lambda s:s.start_sample)
    segment_ticks = [int(s.start_sample) for s in segments]
    for event in wake_events:
        wake = int(event.trigger_sample)
        end = int(event.end_sample) + 1
        previous = bisect_right(starts, wake) - 1
        if previous < 0 or end not in start_set or wake not in wake_start_set:
            continue
        sleep = starts[previous]
        next_start_index = bisect_right(starts, end)
        next_sleep_start = starts[next_start_index] if next_start_index < len(starts) else math.inf
        if (first_between('sleep_validated', sleep, wake) is None
                or first_between('wake_validated', wake, end) is None
                or first_between('sleep_validated', end, next_sleep_start) is None):
            continue
        # An unclosed earlier wake must not be silently interpreted as sleep.
        if wake_ticks[bisect_left(wake_ticks, sleep)] < wake:
            continue
        parts = [s for s in segments[bisect_left(segment_ticks,sleep):bisect_left(segment_ticks,wake)] if s.end_sample < wake]
        pulses = [e for e in background[bisect_left(background_ticks,sleep):bisect_left(background_ticks,wake)] if e.end_sample < wake]
        coverage = [(int(s.start_sample), int(s.end_sample) + 1, int(s.sample_count), s.mean_ua, s.charge_uc) for s in parts]
        coverage += [(int(e.trigger_sample), int(e.end_sample) + 1,
                      round(e.charge_uc / e.mean_ua * sample_rate) if e.mean_ua else 0,
                      e.mean_ua, e.charge_uc) for e in pulses]
        cursor = sleep
        valid = True
        sleep_missing = 0
        sleep_charge = 0.0
        previous_mean = None
        for begin, stop, received, mean, charge in sorted(coverage):
            if begin < cursor or stop <= begin or received <= 0 or received > stop - begin:
                valid = False
                break
            # Internal gaps use constant interpolation at the stored mean.
            # Sleep raw samples are not retained, so endpoint values are unavailable.
            missing = stop - begin - received
            between = begin - cursor
            if between and previous_mean is None:
                valid = False
                break
            sleep_missing += missing + between
            sleep_charge += charge + missing * mean / sample_rate
            if between:
                sleep_charge += between * (previous_mean + mean) / 2 / sample_rate
            cursor = stop
            previous_mean = mean
        if valid and cursor < wake and previous_mean is not None:
            sleep_missing += wake - cursor
            sleep_charge += (wake - cursor) * previous_mean / sample_rate
            cursor = wake
        wake_count = event.charge_uc / event.mean_ua * sample_rate if event.mean_ua else 0
        wake_missing = end - wake - round(wake_count) if math.isfinite(wake_count) else -1
        if (not valid or cursor != wake or not math.isfinite(wake_count)
                or not math.isclose(wake_count, round(wake_count), rel_tol=0, abs_tol=.01)
                or not _small_gap(sleep_missing, wake - sleep, sample_rate)
                or not _small_gap(wake_missing, end - wake, sample_rate)):
            rejected_gaps += 1
            continue
        cycles.append({'sequence': getattr(event, 'sequence', len(cycles) + 1),
                       'peak_ua': getattr(event, 'peak_ua', None),
                       'sleep_start_sample': sleep, 'wake_start_sample': wake, 'sleep_return_sample': end,
                       'sleep_duration_s': (wake - sleep) / sample_rate,
                       'wake_duration_s': (end - wake) / sample_rate,
                       'sleep_charge_uc': sleep_charge,
                       'wake_charge_uc': event.charge_uc + wake_missing * event.mean_ua / sample_rate,
                       'sleep_interpolated_samples': sleep_missing,
                       'wake_interpolated_samples': wake_missing,
                       'interpolated_samples': sleep_missing + wake_missing})
    count = len(cycles)
    voltage = voltage_mv / 1000
    sleep_s = sum(c['sleep_duration_s'] for c in cycles)
    wake_s = sum(c['wake_duration_s'] for c in cycles)
    duration = sleep_s + wake_s
    sleep_charge = sum(c['sleep_charge_uc'] for c in cycles)
    wake_charge = sum(c['wake_charge_uc'] for c in cycles)
    result = {'cycle_count': count, 'excluded_wake_count': len(wake_events) - count,
              'interpolated_cycle_count': sum(c['interpolated_samples'] > 0 for c in cycles),
              'interpolated_samples': sum(c['interpolated_samples'] for c in cycles),
              'interpolated_duration_s': sum(c['interpolated_samples'] for c in cycles) / sample_rate,
              'interpolation_method': 'Stored phase means; adjacent means for gaps between sleep segments.',
              'max_interpolated_phase_s': MAX_INTERPOLATED_PHASE_S,
              'max_interpolated_phase_fraction': MAX_INTERPOLATED_PHASE_FRACTION,
              'excluded_cycles_with_gaps': rejected_gaps, 'voltage_v': voltage,
              'counted_duration_s': duration,
              'average_wake_energy_uwh': wake_charge * voltage / 3600 / count if count else None,
              'average_sleep_energy_uwh': sleep_charge * voltage / 3600 / count if count else None,
              'average_cycle_energy_uwh': (wake_charge + sleep_charge) * voltage / 3600 / count if count else None,
              'average_sleep_current_ua': sleep_charge / sleep_s if sleep_s else None,
              'average_wake_current_ua': wake_charge / wake_s if wake_s else None,
              'average_sleep_duration_s': sleep_s / count if count else None,
              'average_wake_duration_s': wake_s / count if count else None,
              'average_cycle_duration_s': duration / count if count else None,
              'average_wake_power_uw': wake_charge * voltage / duration if duration else None,
              'average_sleep_power_uw': sleep_charge * voltage / duration if duration else None,
              'average_combined_power_uw': (wake_charge + sleep_charge) * voltage / duration if duration else None,
              'wake_duty_cycle_pct': wake_s / duration * 100 if duration else None,
              'assumption': 'Observed complete cycles repeat at the measured voltage; month=30 days, year=365 days.'}
    result['projections'] = {name: project(result, seconds) for name, seconds in PERIODS.items()}
    result['wake_variability'] = _phase_variability(cycles, voltage, 'wake')
    result['sleep_variability'] = _phase_variability(cycles, voltage, 'sleep')
    result['valid_wake_phases'] = [
        {'sequence': c['sequence'], 'start_s': c['wake_start_sample'] / sample_rate,
         'duration_s': c['wake_duration_s'],
         'mean_ua': c['wake_charge_uc'] / c['wake_duration_s'], 'peak_ua': c['peak_ua'],
         'charge_uc': c['wake_charge_uc'], 'energy_uwh': c['wake_charge_uc'] * voltage / 3600,
         'interpolated_samples': c['wake_interpolated_samples']}
        for c in cycles]
    result['valid_sleep_phases'] = [
        {'following_wake_sequence': c['sequence'], 'start_s': c['sleep_start_sample'] / sample_rate,
         'duration_s': c['sleep_duration_s'],
         'mean_ua': c['sleep_charge_uc'] / c['sleep_duration_s'],
         'charge_uc': c['sleep_charge_uc'], 'energy_uwh': c['sleep_charge_uc'] * voltage / 3600,
         'interpolated_samples': c['sleep_interpolated_samples']}
        for c in cycles]
    if custom_duration_s is not None:
        result['projections']['custom'] = project(result, custom_duration_s)
    return result
