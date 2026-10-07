from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(slots=True)
class SampleBatch:
    currents_ua: np.ndarray
    digital: np.ndarray
    # Optional device-tick indices. At 100 kS/s one tick is exactly 10 us.
    # Hardware drivers may leave gaps in this array when the PPK2 sample counter
    # reports dropped data. Drivers may omit it only when the stream is known contiguous.
    sample_ticks: np.ndarray | None = None
    detected_lost_samples: int = 0

    def __post_init__(self) -> None:
        self.currents_ua = np.asarray(self.currents_ua, dtype=np.float32)
        self.digital = np.asarray(self.digital, dtype=np.uint8)
        if self.currents_ua.shape != self.digital.shape:
            raise ValueError("currents_ua and digital must have identical shapes")
        if self.sample_ticks is not None:
            self.sample_ticks = np.asarray(self.sample_ticks, dtype=np.int64)
            if self.sample_ticks.shape != self.currents_ua.shape:
                raise ValueError("sample_ticks must have the same shape as currents_ua")
            if self.sample_ticks.size > 1 and np.any(np.diff(self.sample_ticks) <= 0):
                raise ValueError("sample_ticks must be strictly increasing")
        self.detected_lost_samples = max(0, int(self.detected_lost_samples))

    @property
    def size(self) -> int:
        return int(self.currents_ua.size)


class PowerProfilerDriver(ABC):
    sample_rate_hz: int = 100_000

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_batch(self) -> SampleBatch | None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> SampleBatch | None:
        """Stop acquisition, optionally returning already-received final samples."""
        raise NotImplementedError

    def metadata(self) -> dict[str, Any]:
        return {}

    def close(self) -> None:
        self.stop()
