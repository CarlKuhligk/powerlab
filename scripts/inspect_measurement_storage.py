"""Read-only measurement diagnosis and footer-only preview memory check.

Never decode sample pages or modify the measurement database/raw files.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.storage import event_reader


def peak_working_set():
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.PeakWorkingSetSize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("measurement_id")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    root = args.data_dir.resolve()
    with sqlite3.connect((root / "powerlab.db").as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM measurements WHERE id=?", (args.measurement_id,)).fetchone()
        if row is None:
            raise ValueError("Measurement not found")
        measurement = dict(row)
        events = [dict(event) for event in connection.execute(
            "SELECT id,raw_file,start_sample,end_sample FROM wake_events WHERE measurement_id=?", (args.measurement_id,))]
    settings = json.loads(measurement["settings_json"] or "{}")
    report = {"measurement_id": args.measurement_id, "status": measurement["status"],
              "stored_errors": {key: value for key, value in settings.items() if "error" in key},
              "started_at": measurement["started_at"], "finished_at": measurement["finished_at"], "events": []}
    # Make a sample-page read an explicit failure during this diagnostic.
    original = event_reader.pq.ParquetFile.iter_batches

    def forbidden(*args, **kwargs):
        raise AssertionError("This diagnostic must never decode event sample pages")

    event_reader.pq.ParquetFile.iter_batches = forbidden
    try:
        for event in events:
            path = (root / event["raw_file"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Raw file is outside data directory")
            pf = event_reader.parquet(path)
            rows, groups = pf.metadata.num_rows, pf.metadata.num_row_groups
            del pf
            item = {"event_id": event["id"], "file_bytes": path.stat().st_size, "rows": rows,
                    "row_groups": groups, "full_raw_array_bytes": rows * 13}
            rate = measurement["sample_rate_hz"]
            if measurement["started_at"]:
                started = datetime.fromisoformat(measurement["started_at"]).replace(tzinfo=timezone.utc)
                item["last_sample_time_from_device_ticks"] = (started + timedelta(seconds=event["end_sample"] / rate)).isoformat()
            if rows > event_reader.MAX_RAW_SAMPLES:
                try:
                    event_reader.read_window(root, path)
                    item["full_read_blocked"] = False
                except event_reader.RawDataLimitError:
                    item["full_read_blocked"] = True
                begin = time.monotonic()
                preview, aggregated = event_reader.preview(root, path, max_points=50_000)
                item["footer_preview_seconds"] = round(time.monotonic() - begin, 3)
                item["preview_points"] = len(preview.sample_index)
                item["aggregated"] = aggregated
                del preview
            else:
                item["preview_skipped"] = "Small-event preview requires sample pages; this diagnostic reads footer only."
            item["sample_pages_decoded"] = 0
            report["events"].append(item)
    finally:
        event_reader.pq.ParquetFile.iter_batches = original
    report["peak_process_working_set_bytes"] = peak_working_set()
    out = root / f"storage-diagnosis-{args.measurement_id}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
