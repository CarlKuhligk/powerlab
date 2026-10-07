from __future__ import annotations

import csv
import json
import zipfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:  # Parquet is preferred; NPZ keeps the pilot usable if pyarrow is unavailable.
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - fallback path
    pa = None
    pq = None


@dataclass(slots=True)
class EventRawData:
    sample_index: np.ndarray
    current_ua: np.ndarray
    digital: np.ndarray


class EventStreamWriter:
    """Write immutable wake blocks during acquisition with bounded buffering."""

    def __init__(self, store, measurement_id: str, sequence: int, block_samples: int = 100_000):
        self.store = store
        events = store.measurement_dir(measurement_id) / "events"
        self.path = events / f"event_{sequence:06d}{'.parquet' if pq is not None else '.json'}"
        self.block_samples = block_samples
        self.buffers = []
        self.buffered_samples = 0
        self.writer = None
        self.parts = []
        self.closed = False

    def append(self, indices, current, digital) -> None:
        if self.closed:
            raise RuntimeError("Wake writer is closed")
        offset = 0
        while offset < len(indices):
            take = min(len(indices) - offset, self.block_samples - self.buffered_samples)
            end = offset + take
            self.buffers.append((np.asarray(indices[offset:end], dtype=np.int64).copy(),
                                 np.asarray(current[offset:end], dtype=np.float32).copy(),
                                 np.asarray(digital[offset:end], dtype=np.uint8).copy()))
            self.buffered_samples += take
            offset = end
            if self.buffered_samples == self.block_samples:
                self._flush()

    def _flush(self) -> None:
        if not self.buffers:
            return
        idx, cur, dig = (np.concatenate(parts) for parts in zip(*self.buffers))
        if pq is not None:
            table = pa.table({"sample_index": pa.array(idx), "current_ua": pa.array(cur), "digital": pa.array(dig)})
            if self.writer is None:
                self.writer = pq.ParquetWriter(self.path, table.schema, compression="zstd", compression_level=3)
            self.writer.write_table(table)
        else:
            part = self.path.with_name(f"{self.path.stem}_part_{len(self.parts):06d}.npz")
            np.savez_compressed(part, sample_index=idx, current_ua=cur, digital=dig)
            self.parts.append(str(part.relative_to(self.store.data_dir)))
        self.buffers.clear()
        self.buffered_samples = 0

    def finish(self) -> str:
        if not self.closed:
            try:
                self._flush()
                if pq is None:
                    self.path.write_text(json.dumps({"parts": self.parts}), encoding="utf-8")
            finally:
                try:
                    if self.writer is not None:
                        self.writer.close()
                finally:
                    self.closed = True
                    self.buffers.clear()
                    self.buffered_samples = 0
        return str(self.path.relative_to(self.store.data_dir))

    def abort(self) -> None:
        if not self.closed and self.writer is not None:
            self.writer.close()
        self.closed = True
        self.buffers.clear()
        self.buffered_samples = 0


class RawStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.measurements_dir = self.data_dir / "measurements"
        self.exports_dir = self.data_dir / "exports"
        self.measurements_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self._read_slot = threading.BoundedSemaphore(1)

    def measurement_dir(self, measurement_id: str) -> Path:
        path = self.measurements_dir / measurement_id
        (path / "events").mkdir(parents=True, exist_ok=True)
        return path

    def write_event(
        self,
        measurement_id: str,
        sequence: int,
        sample_index: np.ndarray,
        current_ua: np.ndarray,
        digital: np.ndarray,
    ) -> str:
        events = self.measurement_dir(measurement_id) / "events"
        if pa is not None and pq is not None:
            out = events / f"event_{sequence:06d}.parquet"
            table = pa.table(
                {
                    "sample_index": pa.array(np.asarray(sample_index, dtype=np.int64)),
                    "current_ua": pa.array(np.asarray(current_ua, dtype=np.float32)),
                    "digital": pa.array(np.asarray(digital, dtype=np.uint8)),
                }
            )
            pq.write_table(table, out, compression="zstd", compression_level=6)
        else:  # pragma: no cover - used only if optional wheel install fails
            out = events / f"event_{sequence:06d}.npz"
            np.savez_compressed(
                out,
                sample_index=np.asarray(sample_index, dtype=np.int64),
                current_ua=np.asarray(current_ua, dtype=np.float32),
                digital=np.asarray(digital, dtype=np.uint8),
            )
        return str(out.relative_to(self.data_dir))

    @contextmanager
    def _reading(self):
        from .event_reader import RawDataLimitError
        if not self._read_slot.acquire(blocking=False):
            raise RawDataLimitError("Eine Rohdatenanfrage läuft bereits. Bitte kurz warten.")
        try:
            yield
        finally:
            self._read_slot.release()

    def read_event(self, relative_path: str, *, start_sample=None, end_sample=None, max_samples=2_000_000) -> EventRawData:
        from .event_reader import read_window
        with self._reading():
            return read_window(self.data_dir, self.data_dir / relative_path, start_sample, end_sample, max_samples)

    def event_view(self, relative_path: str, start_sample: int, end_sample: int, max_points: int):
        from .event_reader import view
        with self._reading():
            return view(self.data_dir,self.data_dir/relative_path,start_sample,end_sample,max_points)

    def event_preview(self, relative_path: str, max_points: int = 50_000):
        from .event_reader import preview
        with self._reading():
            return preview(self.data_dir, self.data_dir / relative_path, max_points)

    def stream_event(self, measurement_id: str, sequence: int) -> EventStreamWriter:
        return EventStreamWriter(self, measurement_id, sequence)

    def write_metadata(self, measurement_id: str, payload: dict) -> Path:
        out = self.measurement_dir(measurement_id) / "metadata.json"
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return out

    def create_bundle(
        self,
        measurement_id: str,
        *,
        metadata: dict,
        sleep_segments: list[dict],
        wake_events: list[dict],
        overview_points: list[dict],
        markers: list[dict],
    ) -> Path:
        mdir = self.measurement_dir(measurement_id)
        self.write_metadata(measurement_id, metadata)
        self._write_csv(mdir / "sleep_segments.csv", sleep_segments)
        self._write_csv(mdir / "wake_events.csv", wake_events)
        self._write_csv(mdir / "overview.csv", overview_points)
        self._write_csv(mdir / "markers.csv", markers)

        bundle = self.exports_dir / f"powerlab_{measurement_id}.zip"
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in mdir.rglob("*"):
                if path.is_file():
                    zf.write(path, arcname=path.relative_to(mdir))
        return bundle

    @staticmethod
    def _write_csv(path: Path, rows: list[dict]) -> None:
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        columns: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
