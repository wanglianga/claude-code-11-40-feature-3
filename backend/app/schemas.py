from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str
    password: str


class RunCreateIn(BaseModel):
    rulebook_version_id: int
    model_id: int | None = None
    station_ids: list[int] | None = None
    label: str = Field(default="季节调查覆盖组装", max_length=128)


class RecordIngestIn(BaseModel):
    record_type: Literal[
        "camera_event",
        "patrol_track",
        "device_heartbeat",
        "downtime_log",
        "species_determination",
        "station_metadata",
        "time_calibration",
    ]
    external_id: str = Field(max_length=128)
    station_id: int
    event_time: str
    received_time: str
    source_device: str = Field(max_length=64)
    device_type: str = "camera"
    latitude: float | None = None
    longitude: float | None = None
    coord_precision_m: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RecordReviseIn(BaseModel):
    payload: dict[str, Any]
    reason: str = Field(min_length=1, max_length=500)


class DeterminationIn(BaseModel):
    material_record_id: int
    source: str = Field(max_length=64)
    species_code: str = Field(max_length=64)
    verdict: Literal["present", "absent", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    analyst: str = ""
    notes: str = ""


class DeterminationReviseIn(BaseModel):
    verdict: Literal["present", "absent", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=1000)


class TrendReferenceIn(BaseModel):
    purpose: Literal["absence", "trend"]
    note: str = ""


class RevokeIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class ConflictResolveIn(BaseModel):
    resolution: str = Field(min_length=1, max_length=1000)


class ComparisonAnalyseIn(BaseModel):
    method: Literal["weighted_multi_reference", "single_reference", "descriptive_only"] = (
        "weighted_multi_reference"
    )
    note: str = ""


class ComparisonRecomputeIn(BaseModel):
    method: Literal["weighted_multi_reference", "single_reference", "descriptive_only"] | None = None
    note: str = ""


class EngineeringDatesIn(BaseModel):
    actual_start: str | None = None
    actual_end: str | None = None
    impact_boundary: dict[str, Any] | None = None
    note: str = Field(min_length=1, max_length=1000)


class EngineeringPhaseIn(BaseModel):
    phase_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    actual_start: str
    actual_end: str | None = None
    boundary: dict[str, Any]
    source_type: str = Field(default="施工月报", max_length=64)
    source_ref: str = Field(default="", max_length=128)
    note: str = ""
    concurrent_project_code: str | None = Field(default=None, max_length=64)
    concurrent_project_name: str | None = Field(default=None, max_length=160)


class AvailabilityNoteIn(BaseModel):
    area_id: int
    station_id: int | None = None
    survey_date: str
    kind: Literal["effort_constraint", "weather", "access"] = "effort_constraint"
    effort_scale: float = Field(default=1.0, gt=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
