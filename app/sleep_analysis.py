"""Recorded sleep consumption, including background activity assigned to Sleep."""


def sleep_analysis(sample_rate, voltage_mv, segments, background_events, cycles):
    samples = sum(max(0, int(s.sample_count)) for s in segments)
    samples += sum(max(0, round(e.duration_us * sample_rate / 1e6)) for e in background_events)
    duration = samples / sample_rate
    charge = sum(s.charge_uc for s in segments) + sum(e.charge_uc for e in background_events)
    return {
        'recorded_segment_count': len(segments),
        'sample_count': samples,
        'recorded_duration_s': duration,
        'average_current_ua': charge / duration if samples else None,
        'charge_uc': charge if samples else None,
        'energy_uwh': charge * voltage_mv / 1000 / 3600 if samples else None,
        'background_count': len(background_events),
        'background_charge_uc': sum(e.charge_uc for e in background_events),
        'valid_phase_count': cycles['cycle_count'],
        'average_valid_duration_s': cycles['average_sleep_duration_s'],
        'average_valid_energy_uwh': cycles['average_sleep_energy_uwh'],
        'valid_phases': cycles['valid_sleep_phases'],
    }
