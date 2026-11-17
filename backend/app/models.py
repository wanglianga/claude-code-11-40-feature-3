from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="researcher")  # researcher | auditor | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")

    stations: Mapped[list["Station"]] = relationship(back_populates="zone")


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    elevation_min_m: Mapped[float] = mapped_column(Float)
    elevation_max_m: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    zone: Mapped[Zone] = relationship(back_populates="stations")
    versions: Mapped[list["StationVersion"]] = relationship(
        back_populates="station", order_by="StationVersion.version_number"
    )


class StationVersion(Base):
    __tablename__ = "station_versions"
    __table_args__ = (UniqueConstraint("station_id", "version_number", name="uq_station_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    boundary: Mapped[dict[str, Any]] = mapped_column(JSONB)  # GeoJSON Polygon
    buffer_width_m: Mapped[float] = mapped_column(Float)
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    elevation_min_m: Mapped[float] = mapped_column(Float)
    elevation_max_m: Mapped[float] = mapped_column(Float)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    change_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    station: Mapped[Station] = relationship(back_populates="versions")


class Rulebook(Base):
    __tablename__ = "rulebooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list["RulebookVersion"]] = relationship(
        back_populates="rulebook", order_by="RulebookVersion.version_number"
    )


class RulebookVersion(Base):
    __tablename__ = "rulebook_versions"
    __table_args__ = (UniqueConstraint("rulebook_id", "version_number", name="uq_rulebook_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rulebook_id: Mapped[int] = mapped_column(ForeignKey("rulebooks.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(128))
    season_code: Mapped[str] = mapped_column(String(32))
    season_start: Mapped[date] = mapped_column(Date)
    season_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="active")  # draft | active | retired
    target_species: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    requirements: Mapped[dict[str, Any]] = mapped_column(JSONB)
    route_segments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    elevation_bands: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rulebook: Mapped[Rulebook] = relationship(back_populates="versions")


class ArchiveRecord(Base):
    __tablename__ = "archive_records"
    __table_args__ = (
        Index("ix_archive_station_event", "station_id", "event_time"),
        Index("ix_archive_type_event", "record_type", "event_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_type: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"))
    station_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("station_versions.id"), nullable=True
    )
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_device: Mapped[str] = mapped_column(String(64))
    device_type: Mapped[str] = mapped_column(String(32), default="camera")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    coord_precision_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    record_status: Mapped[str] = mapped_column(String(32), default="active")
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("archive_records.id"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), default="ingest")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    revisions: Mapped[list["RecordRevision"]] = relationship(
        back_populates="record", order_by="RecordRevision.revision_number"
    )


class RecordRevision(Base):
    __tablename__ = "record_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("archive_records.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    received_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    changed_by: Mapped[str] = mapped_column(String(64))
    change_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[ArchiveRecord] = relationship(back_populates="revisions")


class Determination(Base):
    __tablename__ = "determinations"
    __table_args__ = (Index("ix_det_material_status", "material_record_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    material_record_id: Mapped[int] = mapped_column(ForeignKey("archive_records.id"))
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("archive_records.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(64))  # analyst | model | partner
    species_code: Mapped[str] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(32))  # present | absent | uncertain
    confidence: Mapped[float] = mapped_column(Float)
    analyst: Mapped[str] = mapped_column(String(64), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | superseded
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("determinations.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SurveyRun(Base):
    __tablename__ = "survey_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_code: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(128))
    season_code: Mapped[str] = mapped_column(String(32))
    season_start: Mapped[date] = mapped_column(Date)
    season_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | superseded
    rulebook_version_id: Mapped[int] = mapped_column(ForeignKey("rulebook_versions.id"))
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("probability_models.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    units: Mapped[list["SurveyUnit"]] = relationship(back_populates="run")


class SurveyUnit(Base):
    __tablename__ = "survey_units"
    __table_args__ = (
        UniqueConstraint("run_id", "station_id", "zone_id", "unit_date", name="uq_survey_unit"),
        Index("ix_unit_station_date", "station_id", "unit_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("survey_runs.id"), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"))
    unit_date: Mapped[date] = mapped_column(Date)
    station_version_id: Mapped[int] = mapped_column(ForeignKey("station_versions.id"))
    rulebook_version_id: Mapped[int] = mapped_column(ForeignKey("rulebook_versions.id"))
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("probability_models.id"), nullable=True
    )
    assembled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    coverage_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    feature_vector: Mapped[dict[str, Any]] = mapped_column(JSONB)
    gate_status: Mapped[dict[str, Any]] = mapped_column(JSONB)
    data_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)

    run: Mapped[SurveyRun] = relationship(back_populates="units")
    gates: Mapped[list["GateCheck"]] = relationship(back_populates="unit")
    certificate: Mapped["Certificate | None"] = relationship(back_populates="unit", uselist=False)


class GateCheck(Base):
    __tablename__ = "gate_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"), index=True)
    gate: Mapped[str] = mapped_column(String(16))
    requirement_code: Mapped[str] = mapped_column(String(64))
    evaluation_round: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32))  # pass | restricted | fail | pending
    measured: Mapped[dict[str, Any]] = mapped_column(JSONB)
    threshold: Mapped[dict[str, Any]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text)
    rule_ref: Mapped[str] = mapped_column(String(128))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    unit: Mapped[SurveyUnit] = relationship(back_populates="gates")


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (Index("ix_cert_unit_status", "unit_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("survey_runs.id"), index=True)
    certificate_no: Mapped[str] = mapped_column(String(64), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32))  # valid | restricted | invalid | pending
    absence_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    trend_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(String(32), default="normal")  # normal | under_review
    review_reason: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text)
    gate_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    conclusions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("certificates.id"), nullable=True)

    unit: Mapped[SurveyUnit] = relationship(back_populates="certificate")


class ReadingConflict(Base):
    __tablename__ = "reading_conflicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("survey_runs.id"), index=True)
    material_record_id: Mapped[int] = mapped_column(ForeignKey("archive_records.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"))
    species_code: Mapped[str] = mapped_column(String(64))
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open | resolved
    resolution: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProbabilityModel(Base):
    __tablename__ = "probability_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | under_review | retired
    threshold: Mapped[float] = mapped_column(Float)
    calibration_sets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    threshold_review: Mapped[dict[str, Any]] = mapped_column(JSONB)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProbabilityEstimate(Base):
    __tablename__ = "probability_estimates"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("probability_models.id"))
    probability: Mapped[float] = mapped_column(Float)
    interval_low: Mapped[float] = mapped_column(Float)
    interval_high: Mapped[float] = mapped_column(Float)
    threshold_pass: Mapped[bool] = mapped_column(Boolean)
    conclusion: Mapped[str] = mapped_column(String(32))  # supports_partial | below_threshold
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    assumptions: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrendReference(Base):
    __tablename__ = "trend_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_code: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(32))  # absence | trend
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"))
    certificate_id: Mapped[int | None] = mapped_column(ForeignKey("certificates.id"), nullable=True)
    probability_estimate_id: Mapped[int | None] = mapped_column(
        ForeignKey("probability_estimates.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | suspended | revoked
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    suspended_reason: Mapped[str] = mapped_column(Text, default="")
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Wetland restoration comparison workspace.
#
# The workspace never approves engineering work and never scores a project. It
# keeps engineering records (spatial range, phase, actual construction dates and
# source), reads survey applicability from the main service (station version,
# certificate, determinations), and stores comparison plans and analyses with the
# exact input batch they used.
# ---------------------------------------------------------------------------
class ComparisonArea(Base):
    __tablename__ = "comparison_areas"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="reference")  # treatment | reference
    boundary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    elevation_min_m: Mapped[float] = mapped_column(Float)
    elevation_max_m: Mapped[float] = mapped_column(Float)
    habitat: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stations: Mapped[list["ComparisonAreaStation"]] = relationship(
        back_populates="area", order_by="ComparisonAreaStation.id"
    )


class ComparisonAreaStation(Base):
    __tablename__ = "comparison_area_stations"
    __table_args__ = (UniqueConstraint("area_id", "station_id", name="uq_area_station"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("comparison_areas.id"), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))

    area: Mapped[ComparisonArea] = relationship(back_populates="stations")


class EngineeringProject(Base):
    __tablename__ = "engineering_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    project_type: Mapped[str] = mapped_column(String(64), default="wetland_restoration")
    status: Mapped[str] = mapped_column(String(32), default="completed")
    spatial_boundary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    impact_boundary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actual_start: Mapped[date] = mapped_column(Date)
    actual_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), default="工程台账")
    source_ref: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(64), default="seed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    phases: Mapped[list["EngineeringPhase"]] = relationship(
        back_populates="project", order_by="EngineeringPhase.id"
    )


class EngineeringPhase(Base):
    __tablename__ = "engineering_phases"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("engineering_projects.id"), index=True)
    phase_code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    stage_type: Mapped[str] = mapped_column(String(32), default="construction")
    planned_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    boundary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), default="施工月报")
    source_ref: Mapped[str] = mapped_column(String(128), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[EngineeringProject] = relationship(back_populates="phases")


class EngineeringRevision(Base):
    """Append-only record of corrections to engineering records (dates, boundaries, sources)."""

    __tablename__ = "engineering_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("engineering_projects.id"), index=True)
    phase_id: Mapped[int | None] = mapped_column(ForeignKey("engineering_phases.id"), nullable=True)
    field: Mapped[str] = mapped_column(String(64))
    previous: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ComparisonPlan(Base):
    __tablename__ = "comparison_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft | locked
    season_scope: Mapped[list[str]] = mapped_column(JSONB, default=list)
    observation_start: Mapped[date] = mapped_column(Date)
    observation_end: Mapped[date] = mapped_column(Date)
    species_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    treatment_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("engineering_projects.id"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), default="")
    locked_by: Mapped[str] = mapped_column(String(64), default="")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list["ComparisonPlanMember"]] = relationship(
        back_populates="plan", order_by="ComparisonPlanMember.id"
    )


class ComparisonPlanMember(Base):
    __tablename__ = "comparison_plan_members"
    __table_args__ = (UniqueConstraint("plan_id", "area_id", name="uq_plan_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("comparison_plans.id"), index=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("comparison_areas.id"))
    role: Mapped[str] = mapped_column(String(32), default="reference")  # treatment | reference
    note: Mapped[str] = mapped_column(Text, default="")

    plan: Mapped[ComparisonPlan] = relationship(back_populates="members")


class ComparisonAnalysis(Base):
    """A frozen comparison report; later recomputes append a new row and never rewrite it."""

    __tablename__ = "comparison_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_code: Mapped[str] = mapped_column(String(64), unique=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("comparison_plans.id"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))  # computed | descriptive_only | under_review
    method: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    input_batch: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    weights: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    exclusions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    change_log: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    previous_analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("comparison_analyses.id"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SurveyAvailabilityNote(Base):
    """Researcher-recorded effort constraint for an area date (e.g. survey duration halved).

    The note never changes certificates or determinations; it only caps the
    effective effort used by the comparison workspace and is surfaced as a data caveat.
    """

    __tablename__ = "survey_availability_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("comparison_areas.id"), index=True)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"), nullable=True)
    survey_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(64), default="effort_constraint")
    effort_scale: Mapped[float] = mapped_column(Float, default=1.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
