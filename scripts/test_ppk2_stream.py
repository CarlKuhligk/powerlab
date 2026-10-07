"""Standalone PPK2 integrity test used before enabling hardware measurements."""
from __future__ import annotations

import argparse
import struct
import time

from ppk2_api.ppk2_api import PPK2_API, PPK2_MP


def check_sample_counter(raw: bytes) -> tuple[int, list[dict]]:
    usable = len(raw) - len(raw) % 4
    previous = None
    missing_total = 0
    gaps: list[dict] = []
    for sample_index, (word,) in enumerate(struct.iter_unpack("<I", raw[:usable])):
        counter = (word >> 18) & 0x3F
        if previous is not None:
            diff = (counter - previous) & 0x3F
            if diff != 1:
                missing = (diff - 1) & 0x3F
                missing_total += missing
                gaps.append({"sample_index": sample_index, "previous": previous, "current": counter, "missing": missing})
        previous = counter
    return missing_total, gaps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--voltage-mv", type=int, default=3300)
    args = parser.parse_args()

    ppk = PPK2_MP(args.port, buffer_max_size_seconds=5, buffer_chunk_seconds=0.01)
    try:
        try:
            PPK2_API.stop_measuring(ppk)
        except Exception:
            pass
        time.sleep(0.1)
        ppk.ser.reset_input_buffer()
        time.sleep(0.1)

        ppk.get_modifiers()
        print("HW:", ppk.modifiers.get("HW"), "IA:", ppk.modifiers.get("IA"), "Calibrated:", ppk.modifiers.get("Calibrated"))
        ppk.use_source_meter()
        ppk.set_source_voltage(args.voltage_mv)
        print("DUT power remains OFF during this integrity test.")

        raw = bytearray()
        ppk.start_measuring()
        start = time.perf_counter()
        while time.perf_counter() - start < args.duration:
            data = ppk.get_data()
            if data:
                raw.extend(data)
            time.sleep(0.01)
        time.sleep(0.03)
        data = ppk.get_data()
        if data:
            raw.extend(data)
        ppk.stop_measuring()

        samples, _ = ppk.get_samples(bytes(raw))
        missing, gaps = check_sample_counter(bytes(raw))
        print(f"Raw bytes: {len(raw):,}")
        print(f"Decoded samples: {len(samples):,}")
        print(f"Missing samples: {missing:,}")
        print(f"Counter discontinuities: {len(gaps):,}")
        print(f"Sample integrity: {100 * len(samples) / (len(samples) + missing):.8f} %" if samples else "No samples")
    finally:
        try:
            ppk.stop_measuring()
        except Exception:
            pass


if __name__ == "__main__":
    main()
