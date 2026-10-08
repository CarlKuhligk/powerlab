from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BatteryReportRequest(BaseModel):
    energy_wh: float = Field(gt=0, le=1e12, allow_inf_nan=False)
    mode: Literal['sleep', 'duty', 'period']
    value: float = Field(ge=0, le=1e12, allow_inf_nan=False)
    # Optional only for compatibility with the previous timing-curve export.
    range_min: float | None = Field(default=None, ge=0, le=1e12, allow_inf_nan=False)
    range_max: float | None = Field(default=None, gt=0, le=1e12, allow_inf_nan=False)
    # Legacy clients may still send a snapshot; the PDF chart is computed locally.
    chart_png: str | None = Field(default=None, max_length=6_000_000, pattern=r'^data:image/png;base64,')

    @model_validator(mode='after')
    def validate_range(self):
        if self.mode == 'period' and self.value <= 0:
            raise ValueError('Periodendauer muss positiv sein.')
        if self.mode == 'duty' and not 0 < self.value <= 100:
            raise ValueError('Duty-Cycle muss über 0 bis 100 % liegen.')
        if self.range_min is not None or self.range_max is not None:
            if (self.range_min is None or self.range_max is None
                    or not self.range_min <= self.value <= self.range_max or self.range_min >= self.range_max):
                raise ValueError('Einstellung muss innerhalb eines gültigen Kurvenbereichs liegen.')
            if self.mode == 'duty' and (self.range_min <= 0 or self.range_max > 100):
                raise ValueError('Duty-Cycle muss über 0 bis 100 % liegen.')
        return self


class BatteryMeasurementWeight(BaseModel):
    measurement_id: str = Field(min_length=1, max_length=160)
    weight: float = Field(default=1, ge=0, le=1e12, allow_inf_nan=False)


class BatteryMultiReportRequest(BatteryReportRequest):
    sources: list[BatteryMeasurementWeight] = Field(min_length=1, max_length=2000)
    weighting: Literal['mean', 'cycle_count', 'custom'] = 'mean'

    @model_validator(mode='after')
    def validate_sources(self):
        if len({s.measurement_id for s in self.sources}) != len(self.sources):
            raise ValueError('Messungen dürfen nicht mehrfach ausgewählt werden.')
        if self.weighting == 'custom' and not any(s.weight > 0 for s in self.sources):
            raise ValueError('Mindestens ein Gewicht muss positiv sein.')
        return self


class MeasurementStartRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    project: str = Field(default="", max_length=160)
    device: str = Field(default="", max_length=160)
    serial_number: str = Field(default="", max_length=160)
    firmware: str = Field(default="", max_length=160)
    hardware_version: str = Field(default="", max_length=160)
    notes: str = Field(default="", max_length=4000)

    port: str | None = None
    meter_mode: Literal["source", "ampere"] = "source"
    voltage_mv: int = Field(default=3300, ge=800, le=5000)

    start_mode: Literal["now", "scheduled"] = "now"
    scheduled_start_at: datetime | None = None
    stop_mode: Literal["manual", "duration", "end", "wake_count", "sleep_count"] = "manual"
    event_count: int | None = Field(default=None, ge=1, le=1_000_000, strict=True)
    duration_s: float | None = Field(default=None, gt=0, le=31_536_000)
    scheduled_end_at: datetime | None = None

    # Extend this literal and the UI selector together when a reliable new detector is added.
    detection_mode: Literal["threshold", "spectral_compare"] = "threshold"
    spectral_margin_db: float = Field(default=12, ge=3, le=60, allow_inf_nan=False)
    sleep_threshold_ua: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    wake_threshold_ua: float = Field(default=10000.0, gt=0, allow_inf_nan=False)
    sleep_min_s: float = Field(default=5.0, gt=0, le=60, allow_inf_nan=False)
    wake_min_ms: float = Field(default=20.0, gt=0, le=60000, allow_inf_nan=False)
    post_trigger_ms: float = Field(default=1000.0, ge=0, le=5000, allow_inf_nan=False)
    pre_trigger_ms: float = Field(default=1000.0, ge=0, le=5000, allow_inf_nan=False)
    sleep_checkpoint_s: float | None = Field(default=None, ge=0.1, le=3600)

    @model_validator(mode="after")
    def validate_ppk2_and_schedule(self):
        if self.wake_threshold_ua <= self.sleep_threshold_ua:
            raise ValueError("Wake threshold must be greater than sleep threshold")
        if not self.port:
            raise ValueError("A detected PPK2 COM port is required")
        if self.start_mode == "scheduled" and self.scheduled_start_at is None:
            raise ValueError("scheduled_start_at is required for a scheduled measurement")
        if self.stop_mode == "duration" and self.duration_s is None:
            raise ValueError("duration_s is required when stop_mode=duration")
        if self.stop_mode == "end" and self.scheduled_end_at is None:
            raise ValueError("scheduled_end_at is required when stop_mode=end")
        if self.stop_mode in {"wake_count", "sleep_count"} and self.event_count is None:
            raise ValueError("event_count is required for event-count stopping")
        if self.scheduled_start_at and self.scheduled_end_at and self.scheduled_end_at <= self.scheduled_start_at:
            raise ValueError("scheduled_end_at must be after scheduled_start_at")
        return self


class MeasurementPreviewRequest(MeasurementStartRequest):
    """Hardware and detector settings without a persisted measurement or schedule."""
    name: str = "Messvorschau"
    start_mode: Literal["now"] = "now"
    stop_mode: Literal["manual"] = "manual"


class MarkerRequest(BaseModel):
    label: str = Field(min_length=1, max_length=160)


class MeasurementBulkExportRequest(BaseModel):
    measurement_ids: list[str] = Field(min_length=1, max_length=2000)
    kind: Literal["json", "csv", "bundle", "pdf"] = "bundle"


class MeasurementPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    project: str | None = Field(default=None, max_length=160)
    device: str | None = Field(default=None, max_length=160)
    serial_number: str | None = Field(default=None, max_length=160)
    firmware: str | None = Field(default=None, max_length=160)
    hardware_version: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)
