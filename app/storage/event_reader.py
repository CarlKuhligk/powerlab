"""Bound raw-event allocations before decoding any sample payload."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import zipfile

import numpy as np

from .raw_store import EventRawData, pq

MAX_RAW_SAMPLES = 2_000_000
MAX_NPZ_BYTES = 32_000_000
MAX_FOOTER_BYTES = 64_000_000
MAX_ROW_GROUP_BYTES = 64_000_000
BATCH_SAMPLES = 65_536


class RawDataLimitError(RuntimeError):
    """The request cannot be served within the raw-data memory budget."""


def empty() -> EventRawData:
    return EventRawData(np.empty(0, np.int64), np.empty(0, np.float32), np.empty(0, np.uint8))


def parquet(path: Path):
    if pq is None:
        raise RuntimeError("pyarrow is required to read Parquet event files")
    with path.open("rb") as stream:
        stream.seek(-8, 2)
        trailer = stream.read(8)
    if trailer[4:] != b"PAR1":
        raise RuntimeError("Parquet-Datei ist nicht abgeschlossen oder beschädigt")
    if struct.unpack("<I", trailer[:4])[0] > MAX_FOOTER_BYTES:
        raise RawDataLimitError("Parquet-Metadaten überschreiten das sichere Speicherlimit.")
    return pq.ParquetFile(path, pre_buffer=False)


def parts(root: Path, path: Path):
    if path.suffix.lower() != ".json":
        yield path
        return
    if path.stat().st_size > 8_000_000:
        raise RawDataLimitError("Event-Manifest überschreitet das sichere Speicherlimit.")
    for name in json.loads(path.read_text(encoding="utf-8"))["parts"]:
        child = (root / name).resolve()
        if not child.is_relative_to(root.resolve()) or child.suffix.lower() != ".npz":
            raise RuntimeError("Invalid raw event part")
        yield child


def npz_rows(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        if sum(member.file_size for member in archive.infolist()) > MAX_NPZ_BYTES:
            raise RawDataLimitError("NPZ-Rohdatenblock überschreitet das sichere Speicherlimit.")
        with archive.open("sample_index.npy") as stream:
            version = np.lib.format.read_magic(stream)
            header = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                      else np.lib.format.read_array_header_2_0)(stream)
            shape, _, dtype = header
            if len(shape) != 1 or dtype != np.dtype("int64"):
                raise RuntimeError("Invalid sample_index array")
            return shape[0]


def row_bounds(group):
    stats = group.column(0).statistics
    return (int(stats.min), int(stats.max)) if stats and stats.has_min_max else (None, None)


def count_rows(root: Path, path: Path) -> int:
    return sum(npz_rows(part) if part.suffix.lower() == ".npz" else parquet(part).metadata.num_rows
               for part in parts(root, path))


def check_range(start_sample, end_sample):
    if (start_sample is None) != (end_sample is None):
        raise RawDataLimitError("Für Rohdaten bitte Anfang und Ende des Zeitfensters angeben.")
    if start_sample is not None and (end_sample < start_sample or end_sample - start_sample + 1 > MAX_RAW_SAMPLES):
        raise RawDataLimitError("Rohdaten-Zeitfenster zu groß: höchstens 2 Millionen Samples pro Anfrage.")


def iter_batches(root: Path, path: Path, start_sample=None, end_sample=None):
    for part in parts(root, path):
        if part.suffix.lower() == ".npz":
            npz_rows(part)  # Inspect uncompressed size before np.load allocates arrays.
            with np.load(part, allow_pickle=False) as data:
                raw = EventRawData(data["sample_index"], data["current_ua"], data["digital"])
                if start_sample is None:
                    yield raw
                else:
                    mask = (raw.sample_index >= start_sample) & (raw.sample_index <= end_sample)
                    if np.any(mask):
                        yield EventRawData(raw.sample_index[mask], raw.current_ua[mask], raw.digital[mask])
            continue
        pf = parquet(part)
        for number in range(pf.metadata.num_row_groups):
            group = pf.metadata.row_group(number)
            lo, hi = row_bounds(group)
            if start_sample is not None and lo is not None and (hi < start_sample or lo > end_sample):
                continue
            if group.total_byte_size > MAX_ROW_GROUP_BYTES:
                raise RawDataLimitError("Parquet-Rohdatenblock überschreitet das sichere Speicherlimit.")
            for batch in pf.iter_batches(batch_size=BATCH_SAMPLES, row_groups=[number],
                                         columns=["sample_index", "current_ua", "digital"], use_threads=False):
                arrays = [batch.column(i).to_numpy(zero_copy_only=False) for i in range(3)]
                if start_sample is not None:
                    mask = (arrays[0] >= start_sample) & (arrays[0] <= end_sample)
                    if not np.any(mask):
                        continue
                    arrays = [array[mask] for array in arrays]
                yield EventRawData(*arrays)


def read_window(root: Path, path: Path, start_sample=None, end_sample=None, max_samples=MAX_RAW_SAMPLES):
    max_samples = min(max_samples, MAX_RAW_SAMPLES)
    check_range(start_sample, end_sample)
    if start_sample is None and count_rows(root, path) > max_samples:
        raise RawDataLimitError("Event zu groß zum vollständigen Laden. Bitte ein kurzes Zeitfenster wählen oder das Rohdaten-Bundle exportieren.")
    chunks = []
    count = 0
    for batch in iter_batches(root, path, start_sample, end_sample):
        count += len(batch.sample_index)
        if count > max_samples:
            raise RawDataLimitError("Rohdaten überschreiten das sichere Speicherlimit.")
        chunks.append(batch)
    if not chunks:
        return empty()
    return EventRawData(*(np.concatenate([getattr(chunk, field) for chunk in chunks])
                          for field in ("sample_index", "current_ua", "digital")))


def preview(root: Path, path: Path, max_points: int):
    """Large Parquet events use footer extrema, with explicitly approximate times."""
    if path.suffix.lower() in {".json", ".npz"}:
        return read_window(root, path), False
    pf = parquet(path)
    if pf.metadata.num_rows <= MAX_RAW_SAMPLES:
        return read_window(root, path), False
    # Coalesce group statistics into fixed display buckets, without decoding pages.
    bins = max(1, (max_points - 2) // 2)
    groups_per_bin = max(1, (pf.metadata.num_row_groups + bins - 1) // bins)
    idx, cur = [], []
    low = high = None
    first = last = None
    for number in range(pf.metadata.num_row_groups):
        group = pf.metadata.row_group(number)
        lo, hi = row_bounds(group)
        stats = group.column(1).statistics
        if lo is None or not stats or not stats.has_min_max:
            raise RawDataLimitError("Großes Event ohne sichere Blockstatistik. Bitte ein kurzes Zeitfenster wählen.")
        if first is None:
            first = lo
        last = hi
        if low is None or stats.min < low[1]:
            low = (lo, float(stats.min))
        if high is None or stats.max > high[1]:
            high = (hi, float(stats.max))
        if (number + 1) % groups_per_bin == 0 or number == pf.metadata.num_row_groups - 1:
            for position, value in sorted((low, high)):
                idx.append(position)
                cur.append(value)
            low = high = None
    # Extrema timestamps represent containing blocks, not original sample times.
    if idx and idx[0] != first:
        idx.insert(0, first); cur.insert(0, cur[0])
    if idx and idx[-1] != last:
        idx.append(last); cur.append(cur[-1])
    return EventRawData(np.asarray(idx, np.int64), np.asarray(cur, np.float32), np.zeros(len(idx), np.uint8)), True


def view(root: Path, path: Path, start_sample: int, end_sample: int, max_points: int):
    """Exact-time min/max envelopes, streaming with O(batch + point budget) RAM."""
    if end_sample < start_sample or max_points < 4:
        raise RawDataLimitError("Invalid view range or point budget")
    if end_sample-start_sample+1 <= max_points:
        raw=read_window(root,path,start_sample,end_sample,max_samples=max_points)
        return raw,len(raw.sample_index)
    bins=max(1,(max_points-2)//2)
    lows,highs=np.full(bins,np.inf),np.full(bins,-np.inf)
    low_idx,high_idx=np.full(bins,-1,np.int64),np.full(bins,-1,np.int64)
    low_digital,high_digital=np.zeros(bins,np.uint8),np.zeros(bins,np.uint8)
    first=last=None
    count=0
    exact=[]
    for batch in iter_batches(root,path,start_sample,end_sample):
        idx,current,digital=batch.sample_index,batch.current_ua,batch.digital
        count+=len(idx)
        if exact is not None:
            if count<=max_points:
                exact.append(batch)
            else:
                exact=None
        if first is None:
            first=(int(idx[0]),float(current[0]),int(digital[0]))
        last=(int(idx[-1]),float(current[-1]),int(digital[-1]))
        slots=((idx-start_sample)*bins//(end_sample-start_sample+1)).astype(int)
        cuts=np.flatnonzero(slots[1:] != slots[:-1])+1
        for begin,stop in zip(np.r_[0,cuts],np.r_[cuts,len(idx)]):
            slot=int(slots[begin])
            block=current[begin:stop]
            lo=begin+int(np.argmin(block));hi=begin+int(np.argmax(block))
            if current[lo]<lows[slot]:
                lows[slot],low_idx[slot],low_digital[slot]=current[lo],idx[lo],digital[lo]
            if current[hi]>highs[slot]:
                highs[slot],high_idx[slot],high_digital[slot]=current[hi],idx[hi],digital[hi]
    if first is None:
        return empty(),0
    if exact is not None:
        return EventRawData(*(np.concatenate([getattr(chunk,field) for chunk in exact])
                              for field in ("sample_index","current_ua","digital"))),count
    points={first[0]:(first[1],first[2]),last[0]:(last[1],last[2])}
    for positions,values,digits in ((low_idx,lows,low_digital),(high_idx,highs,high_digital)):
        for i,value,digit in zip(positions,values,digits):
            if i>=0:
                points[int(i)]=(float(value),int(digit))
    positions=sorted(points)
    return EventRawData(np.asarray(positions,np.int64),np.asarray([points[i][0] for i in positions],np.float32),
                        np.asarray([points[i][1] for i in positions],np.uint8)),count
