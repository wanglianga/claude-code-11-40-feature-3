"""Wetland restoration survey-comparison workspace.

This module answers one question for researchers: *which observed differences
between a restored area and candidate reference areas have a comparable basis,
and which do not yet support an engineering-impact judgement?*

Design boundaries (also enforced in tests):

* The workspace only reads survey applicability from the main service — station
  versions, current certificates, gate measurements and determinations. It never
  signs or overrides a certificate and never approves engineering work.
* It never scores or ranks a project automatically; differences are reported as
  observation-only comparisons with explicit scope limits.
* Reference weights are fitted **exclusively on pre-restoration qualifying
  surveys**. Post-restoration outcomes are never used to pick or tune the
  combination.
* Effective effort is calibrated per unit before any rate is compared: a photo
  count is never substituted for a comparable observation rate.
* Construction impact is decided from the engineering record's *actual spatial
  range and dates*, never from administrative zone membership.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from sqlalchemy import func, select
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from .models import (
    ArchiveRecord,
    AuditEvent,
    Certificate,
    ComparisonAnalysis,
    ComparisonArea,
    ComparisonPlan,
    Determination,
    EngineeringPhase,
    EngineeringProject,
    EngineeringRevision,
    GateCheck,
    Station,
    SurveyAvailabilityNote,
    SurveyUnit,
    utcnow,
)
from .utils import canonical_hash

DEFAULT_CRITERIA: dict[str, Any] = {
    # A comparison needs a shared pre-restoration window; below this the analysis
    # degrades to descriptive differences instead of forcing a fit.
    "min_common_pre_dates": 2,
    "min_pre_dates_per_area": 2,
    "min_post_dates_per_area": 1,
    "min_post_references": 2,
    "data_impact_threshold_pct": 33.0,
    "min_weight_share": 0.05,
    "max_weight_spread_pct": 60.0,
    "leave_one_out_tolerance_pct": 15.0,
    "min_retained_weight_share": 0.6,
}

METHOD_WEIGHTED = "weighted_multi_reference"
METHOD_SINGLE = "single_reference"
METHOD_DESCRIPTIVE = "descriptive_only"


class DomainError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------- helpers
def _ring(geometry: dict[str, Any]) -> Any:
    try:
        return shape(geometry)
    except Exception:  # noqa: BLE001 - malformed geometry is reported upstream
        return None


def _area_contains(geometry: dict[str, Any], lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    geom = _ring(geometry)
    return bool(geom is not None and geom.covers(Point(lon, lat)))


def effective_impact_geometry(project: EngineeringProject) -> Any:
    """Union of the project impact boundary and every phase's actual boundary."""
    parts = []
    base = _ring(project.impact_boundary or project.spatial_boundary)
    if base is not None:
        parts.append(base)
    for phase in project.phases:
        if phase.boundary:
            geom = _ring(phase.boundary)
            if geom is not None:
                parts.append(geom)
    return unary_union(parts) if parts else None


def effective_zone_impact_geometry(project: EngineeringProject) -> Any:
    """Project-wide impact used for calendar exclusions (union of phases' boundaries)."""
    return effective_impact_geometry(project)


def dates_in(start: date | None, end: date | None, dates: list[date]) -> list[date]:
    if start is None:
        return []
    return [d for d in dates if d >= start and (end is None or d <= end)]


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - pos) + ordered[high] * (pos - low)


def _nice_round(value: float, step: float = 0.05) -> float:
    return round(round(value / step) * step, 4)


GATE_LABELS = {
    "device": "设备可用性",
    "time": "时间可信度",
    "space": "空间覆盖",
    "reading": "素材判读",
}


# ------------------------------------------------------------------------- data
def areas_for_plan(db, plan: ComparisonPlan) -> list[ComparisonArea]:
    ids = [m.area_id for m in plan.members]
    if not ids:
        return []
    return list(
        db.execute(select(ComparisonArea).where(ComparisonArea.id.in_(ids)).order_by(ComparisonArea.id))
        .scalars()
    )


def area_station_ids(db, area: ComparisonArea) -> list[int]:
    return [link.station_id for link in area.stations]


def plan_criteria(plan: ComparisonPlan) -> dict[str, Any]:
    merged = dict(DEFAULT_CRITERIA)
    merged.update(plan.criteria or {})
    return merged


def _station_points(db, station_ids: list[int]) -> dict[int, Station]:
    if not station_ids:
        return {}
    return {
        s.id: s
        for s in db.execute(select(Station).where(Station.id.in_(station_ids))).scalars()
    }


def _units_between(db, station_ids: list[int], start: date, end: date) -> list[SurveyUnit]:
    """Latest assembled unit per (station, date) inside the observation window."""
    if not station_ids:
        return []
    from .models import RulebookVersion

    rows = list(
        db.execute(
            select(SurveyUnit)
            .join(RulebookVersion, SurveyUnit.rulebook_version_id == RulebookVersion.id)
            .where(SurveyUnit.station_id.in_(station_ids))
            .where(SurveyUnit.unit_date >= start)
            .where(SurveyUnit.unit_date <= end)
            .order_by(
                SurveyUnit.station_id,
                SurveyUnit.unit_date,
                RulebookVersion.version_number.desc(),
                SurveyUnit.id.desc(),
            )
        ).scalars()
    )
    # Latest assembled unit wins, so a rebuilt run (which re-reads current records)
    # replaces the older run's certificate state for the same station and date.
    latest: dict[tuple[int, date], SurveyUnit] = {}
    for unit in rows:
        latest.setdefault((unit.station_id, unit.unit_date), unit)
    return sorted(latest.values(), key=lambda u: (u.station_id, u.unit_date))


def _unit_gates(db, unit_ids: list[int]) -> dict[int, dict[str, GateCheck]]:
    if not unit_ids:
        return {}
    gates: dict[int, dict[str, GateCheck]] = {}
    for gate in db.execute(select(GateCheck).where(GateCheck.unit_id.in_(unit_ids))).scalars():
        gates.setdefault(gate.unit_id, {})[gate.gate] = gate
    return gates


def _certificates(db, unit_ids: list[int]) -> dict[int, Certificate]:
    if not unit_ids:
        return {}
    rows = db.execute(
        select(Certificate)
        .where(Certificate.unit_id.in_(unit_ids))
        .where(Certificate.superseded_by_id.is_(None))
        .order_by(Certificate.version.desc())
    ).scalars()
    result: dict[int, Certificate] = {}
    for cert in rows:
        result.setdefault(cert.unit_id, cert)
    return result


WATERBIRD_SPECIES = {
    "ANSER_INDICUS": "斑头雁",
    "ANAS_CRECCA": "绿翅鸭",
    "ARD_CINEREA": "苍鹭",
}


def _family_of(species_code: str, material_payload: dict[str, Any] | None = None) -> str:
    if material_payload:
        manual = material_payload.get("species_group") or material_payload.get("manual_family")
        if manual:
            return str(manual)
    code = (species_code or "").upper()
    if code == "LOPHOPHORUS":
        return "galliformes"
    if code in {"BUDORCAS", "AILUROPODA"}:
        return "large_mammal"
    if code in WATERBIRD_SPECIES:
        return "waterbird"
    return "other"


FAMILY_LABELS = {
    "galliformes": "雉类（绿尾虹雉）",
    "large_mammal": "大型兽类（羚牛 / 大熊猫）",
    "waterbird": "湿地水鸟（斑头雁 / 绿翅鸭 / 苍鹭）",
    "other": "其他",
}


def _unit_observations(
    db, units: list[SurveyUnit], criteria: dict[str, Any]
) -> dict[int, dict[str, Any]]:
    """Per-unit calibrated observation counts, by species family.

    The count is built from *qualifying* determinations on material records that
    belong to the unit's station and local survey date. A raw photo count is never
    used: only determinations that clear the confidence threshold and resolve to a
    known family enter the rate, and the rate is divided by calibrated effort.
    """
    min_conf = float(criteria.get("min_confidence", 0.7))
    species_scope = {str(code).upper() for code in criteria.get("species_codes") or []}
    if not units:
        return {}
    station_ids = sorted({u.station_id for u in units})
    date_keys = {(u.station_id, u.unit_date) for u in units}
    records = list(
        db.execute(
            select(ArchiveRecord)
            .where(ArchiveRecord.station_id.in_(station_ids))
            .where(ArchiveRecord.record_type == "camera_event")
        ).scalars()
    )
    from .utils import local_date

    material_ids: list[int] = []
    by_unit_materials: dict[int, list[ArchiveRecord]] = {}
    for rec in records:
        key = (rec.station_id, local_date(rec.event_time))
        if key not in date_keys:
            continue
        for unit in units:
            if unit.station_id == rec.station_id and unit.unit_date == key[1]:
                by_unit_materials.setdefault(unit.id, []).append(rec)
                material_ids.append(rec.id)
                break
    dets: dict[int, list[Determination]] = {}
    if material_ids:
        for det in db.execute(
            select(Determination)
            .where(Determination.material_record_id.in_(material_ids))
            .where(Determination.status == "active")
        ).scalars():
            dets.setdefault(det.material_record_id, []).append(det)

    result: dict[int, dict[str, Any]] = {}
    for unit in units:
        materials = by_unit_materials.get(unit.id, [])
        families: dict[str, dict[str, Any]] = {}
        for material in materials:
            for det in dets.get(material.id, []):
                if det.confidence < min_conf:
                    continue
                if det.verdict == "uncertain":
                    continue
                if species_scope and det.species_code.upper() not in species_scope:
                    continue
                family = _family_of(det.species_code, material.payload)
                bucket = families.setdefault(
                    family, {"detections": 0, "observations": 0, "present_species": []}
                )
                bucket["observations"] += 1
                if det.verdict == "present":
                    bucket["detections"] += 1
                    if det.species_code not in bucket["present_species"]:
                        bucket["present_species"].append(det.species_code)
        result[unit.id] = {
            "families": families,
            "material_ids": [m.id for m in materials],
            "material_external_ids": [m.external_id for m in materials],
        }
    return result


def calibrate_effort(
    device_measured: dict[str, Any], reading_measured: dict[str, Any]
) -> dict[str, Any]:
    """Calibrated effective effort for one survey unit.

    ``effective_effort_days`` is available camera-hours corrected by the share of
    material that actually received a qualifying reading. Photo totals are kept
    only as a transparency value and are explicitly not used as an observation
    rate.
    """
    available_hours = float(device_measured.get("available_hours") or 0.0)
    pending_ratio = float(reading_measured.get("pending_ratio") or 0.0)
    quality = max(0.0, 1.0 - pending_ratio)
    effort_days = available_hours / 24.0 * quality
    payload = {
        "available_hours": round(available_hours, 2),
        "pending_ratio": round(pending_ratio, 3),
        "observation_quality": round(quality, 3),
        "effective_effort_days": round(max(effort_days, 0.25), 3),
        "material_count": int(device_measured.get("frame_count") or 0),
        "note": "有效努力量=可用相机小时/24×判读完成比例；照片总数仅作核对，不代入观测率。",
    }
    fingerprint = canonical_hash(
        {
            "available_hours": payload["available_hours"],
            "pending_ratio": payload["pending_ratio"],
            "material_count": payload["material_count"],
        }
    )
    payload["calibration_id"] = f"CAL-{fingerprint[:10]}"
    return payload


# ------------------------------------------------------------------ assessment
def expected_survey_dates(db, plan: ComparisonPlan) -> list[date]:
    from .models import RulebookVersion

    scopes = set(plan.season_scope or [])
    if not scopes:
        return []
    versions = list(
        db.execute(
            select(RulebookVersion).where(RulebookVersion.season_code.in_(scopes))
        ).scalars()
    )
    latest: dict[str, RulebookVersion] = {}
    for version in versions:
        key = version.season_code
        if key not in latest or version.version_number > latest[key].version_number:
            latest[key] = version
    dates: set[date] = set()
    for version in latest.values():
        for raw in (version.requirements.get("schedule", {}) or {}).get("survey_dates", []):
            d = date.fromisoformat(raw)
            if plan.observation_start <= d <= plan.observation_end:
                dates.add(d)
    return sorted(dates)


def habitat_similarity(treatment: ComparisonArea, candidate: ComparisonArea) -> float:
    treat = treatment.habitat or {}
    cand = candidate.habitat or {}
    score = 0.0
    total = 0.0
    if treat.get("code") or cand.get("code"):
        total += 1.0
        score += 1.0 if treat.get("code") == cand.get("code") else 0.0
    if treat.get("elevation_band") or cand.get("elevation_band"):
        total += 1.0
        score += 1.0 if treat.get("elevation_band") == cand.get("elevation_band") else 0.0
    t_min, t_max = treatment.elevation_min_m, treatment.elevation_max_m
    c_min, c_max = candidate.elevation_min_m, candidate.elevation_max_m
    span = max(1.0, max(t_max, c_max) - min(t_min, c_min))
    overlap = max(0.0, min(t_max, c_max) - max(t_min, c_min))
    total += 1.0
    score += overlap / span
    return round(score / total, 3) if total else 0.0


def _project_windows(
    db, area: ComparisonArea, stations: list[Station]
) -> list[dict[str, Any]]:
    """Construction windows whose *actual* range covers this area's stations.

    Every project is considered, including the treatment project: a phase that
    spills into a neighbouring reference area must exclude that reference's days
    even though the two areas belong to different administrative zones.
    """
    projects = list(db.execute(select(EngineeringProject)).scalars())
    windows: list[dict[str, Any]] = []
    area_geom = _ring(area.boundary)

    def geom_covers(geom) -> bool:
        if geom is None:
            return False
        if area_geom is not None and geom.intersects(area_geom):
            return True
        return any(geom.covers(Point(station.longitude, station.latitude)) for station in stations)

    for project in projects:
        phases = list(project.phases) or []
        # Project-level footprint only. Phase footprints are tested per phase below;
        # unioning them here would let one spill-over phase drag every other phase
        # onto the reference area.
        project_covers = geom_covers(_ring(project.impact_boundary or project.spatial_boundary))
        if not phases:
            if project_covers:
                start, end = project.actual_start, project.actual_end
                windows.append(
                    {
                        "project_id": project.id,
                        "project_code": project.code,
                        "project_name": project.name,
                        "phase_id": None,
                        "phase_code": None,
                        "start": start,
                        "end": end,
                        "source_type": project.source_type,
                        "source_ref": project.source_ref,
                    }
                )
            continue
        for phase in phases:
            # A phase with its own footprint is judged by that footprint alone.
            covered = geom_covers(_ring(phase.boundary)) if phase.boundary else project_covers
            if covered:
                windows.append(
                    {
                        "project_id": project.id,
                        "project_code": project.code,
                        "project_name": project.name,
                        "phase_id": phase.id,
                        "phase_code": phase.phase_code,
                        "phase_name": phase.name,
                        "start": phase.actual_start or project.actual_start,
                        "end": phase.actual_end or project.actual_end,
                        "source_type": phase.source_type,
                        "source_ref": phase.source_ref,
                    }
                )
    return windows


def _impact_on_date(windows: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    for window in windows:
        start, end = window.get("start"), window.get("end")
        if start is None:
            continue
        if day >= start and (end is None or day <= end):
            return window
    return None


def build_area_observation(
    db,
    area: ComparisonArea,
    plan: ComparisonPlan,
    criteria: dict[str, Any],
    project_id: int | None,
    role: str | None = None,
) -> dict[str, Any]:
    station_ids = area_station_ids(db, area)
    stations = list(
        db.execute(select(Station).where(Station.id.in_(station_ids)).order_by(Station.id)).scalars()
    ) if station_ids else []
    units = _units_between(db, station_ids, plan.observation_start, plan.observation_end)
    gates = _unit_gates(db, [u.id for u in units])
    certs = _certificates(db, [u.id for u in units])
    observations = _unit_observations(db, units, criteria)
    area_role = role or area.role
    windows = _project_windows(db, area, stations)
    station_map = {s.id: s for s in stations}
    notes = list(
        db.execute(
            select(SurveyAvailabilityNote).where(SurveyAvailabilityNote.area_id == area.id)
        ).scalars()
    )
    notes_by_date: dict[date, list[SurveyAvailabilityNote]] = {}
    for note in notes:
        notes_by_date.setdefault(note.survey_date, []).append(note)
    rows: list[dict[str, Any]] = []
    for unit in units:
        gate_map = gates.get(unit.id, {})
        device = gate_map.get("device")
        reading = gate_map.get("reading")
        effort = calibrate_effort(
            (device.measured if device else {}) or {},
            (reading.measured if reading else {}) or {},
        )
        day_notes = notes_by_date.get(unit.unit_date, [])
        applied_notes = []
        for note in day_notes:
            if note.effort_scale < 1.0:
                effort["effective_effort_days"] = round(
                    max(effort["effective_effort_days"] * note.effort_scale, 0.25), 3
                )
            applied_notes.append(
                {
                    "note_id": note.id,
                    "kind": note.kind,
                    "effort_scale": note.effort_scale,
                    "reason": note.reason,
                    "created_by": note.created_by,
                }
            )
        if applied_notes:
            effort["availability_notes"] = applied_notes
            effort["note"] += " 已按录入的调查可用性说明折算有效努力量（不修改原始档案记录）。"
        obs = observations.get(unit.id, {"families": {}, "material_ids": [], "material_external_ids": []})
        detections = sum(b["detections"] for b in obs["families"].values())
        observations_total = sum(b["observations"] for b in obs["families"].values())
        cert = certs.get(unit.id)
        status = cert.status if cert else "missing"
        exclusion: str | None = None
        exclusion_detail = ""
        impact = _impact_on_date(windows, unit.unit_date)
        if impact is not None and not (
            area_role == "treatment" and impact["project_id"] == project_id
        ):
            # The treatment area's own construction defines its pre/post split; it is
            # not an exclusion. Any other construction reaching it still excludes.
            exclusion = "construction_impact"
            exclusion_detail = (
                f"调查日 {unit.unit_date} 落在 {impact['project_code']}"
                f"{' / ' + (impact.get('phase_code') or '') if impact.get('phase_code') else ''} 的"
                f"实际施工期 {_iso(impact.get('start'))}–{_iso(impact.get('end'))}，"
                "且按实际影响范围覆盖本区域（与行政归属无关），该单元不进入比较。"
            )
        elif cert is None:
            exclusion = "no_certificate"
            exclusion_detail = "该调查日无证书记录，不能读取调查适用性。"
        elif cert.review_status == "under_review":
            exclusion = "under_review"
            exclusion_detail = f"证书 {cert.certificate_no} 复审中，暂停新比较引用；区域观测事实仍保留可查。"
        elif not cert.trend_eligible:
            exclusion = "certificate_not_comparable"
            exclusion_detail = (
                f"证书 {cert.certificate_no} 状态 {cert.status}，不满足趋势输入条件，"
                "不能作为可比调查单元。"
            )
        rate = round(detections / effort["effective_effort_days"], 4) if detections else 0.0
        rows.append(
            {
                "unit_id": unit.id,
                "station_id": unit.station_id,
                "station_code": station_map[unit.station_id].code if unit.station_id in station_map else None,
                "zone_id": unit.zone_id,
                "date": unit.unit_date.isoformat(),
                "certificate_no": cert.certificate_no if cert else None,
                "certificate_status": status,
                "review_status": cert.review_status if cert else None,
                "trend_eligible": bool(cert.trend_eligible) if cert else False,
                "gate_status": unit.gate_status,
                "exclusion": exclusion,
                "exclusion_detail": exclusion_detail,
                "detections": detections,
                "observations": observations_total,
                "families": obs["families"],
                "effective_effort": effort,
                "availability_notes": applied_notes,
                "observation_rate": rate,
                "material_record_ids": obs["material_ids"],
                "evidence_record_ids": sorted(
                    {int(v) for g in gate_map.values() for v in (g.evidence or []) if isinstance(v, int)}
                ),
                "impact_window": (
                    {
                        "project_id": impact["project_id"],
                        "project_code": impact["project_code"],
                        "phase_id": impact.get("phase_id"),
                        "phase_code": impact.get("phase_code"),
                        "start": _iso(impact.get("start")),
                        "end": _iso(impact.get("end")),
                        "source_type": impact.get("source_type"),
                        "source_ref": impact.get("source_ref"),
                    }
                    if impact
                    else None
                ),
            }
        )
    expected_in_area = [d.isoformat() for d in expected_survey_dates(db, plan)]
    present_dates = {row["date"] for row in rows}
    # A date with no assembled unit is recorded as a gap; the analysis never treats
    # an absent survey day as a zero observation.
    missing = [d for d in expected_in_area if d not in present_dates]
    missing_detail = [
        {
            "date": d,
            "reason": "该调查日没有组装调查单元（部分季节调查缺失），不计为 0 次观测。",
        }
        for d in missing
    ]
    usable = [row for row in rows if row["exclusion"] is None]
    return {
        "area_id": area.id,
        "code": area.code,
        "name": area.name,
        "role": area_role,
        "zone": None,
        "station_codes": [s.code for s in stations],
        "station_ids": station_ids,
        "elevation_range_m": [area.elevation_min_m, area.elevation_max_m],
        "habitat": area.habitat,
        "units": rows,
        "counts": {
            "units": len(rows),
            "usable": len(usable),
            "excluded_impact": sum(1 for r in rows if r["exclusion"] == "construction_impact"),
            "excluded_other": sum(
                1 for r in rows if r["exclusion"] not in {None, "construction_impact"}
            ),
        },
        "expected_dates": expected_in_area,
        "missing_dates": missing,
        "missing_detail": missing_detail,
        "construction_windows": [
            {
                "project_id": w["project_id"],
                "project_code": w["project_code"],
                "project_name": w["project_name"],
                "phase_id": w.get("phase_id"),
                "phase_code": w.get("phase_code"),
                "phase_name": w.get("phase_name"),
                "start": _iso(w.get("start")),
                "end": _iso(w.get("end")),
                "source_type": w.get("source_type"),
                "source_ref": w.get("source_ref"),
            }
            for w in windows
        ],
    }


# -------------------------------------------------------------------- analysis
def _date_sets(area_obs: dict[str, Any]) -> list[str]:
    return sorted({row["date"] for row in area_obs["units"] if row["exclusion"] is None})


def _usable_by_date(area_obs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in area_obs["units"]:
        if row["exclusion"] is not None:
            continue
        result[row["date"]] = row
    return result


def _rate_series(area_obs: dict[str, Any], dates: list[str]) -> dict[str, float]:
    by_date = _usable_by_date(area_obs)
    return {d: by_date[d]["observation_rate"] for d in dates if d in by_date}


def _fit_weights(
    treatment: dict[str, Any],
    candidates: list[dict[str, Any]],
    pre_dates: list[str],
    criteria: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fit pre-restoration weights normalised to the treatment's own observed level.

    The fit only ever sees pre-restoration dates, so post-restoration outcomes
    cannot steer the combination toward the most flattering reference set.
    """
    fitted: list[dict[str, Any]] = []
    min_share = float(criteria.get("min_weight_share", 0.05))
    for cand in candidates:
        common = [
            d
            for d in pre_dates
            if d in _usable_by_date(treatment) and d in _usable_by_date(cand)
        ]
        t_series = _rate_series(treatment, common)
        c_series = _rate_series(cand, common)
        fit_error = None
        coverage = 0.0
        if common:
            coverage = round(len(common) / max(1, len(pre_dates)), 3)
            errors = [abs(t_series[d] - c_series[d]) for d in common]
            fit_error = round(sum(errors) / len(errors), 4)
        fitted.append(
            {
                "area_id": cand["area_id"],
                "code": cand["code"],
                "name": cand["name"],
                "common_pre_dates": common,
                "pre_date_coverage": coverage,
                "pre_fit_error": fit_error,
                "habitat_similarity": cand["habitat_similarity"],
                "series": {"pre": t_series, "candidate": c_series},
            }
        )
    # Weight = habitat similarity × pre-window coverage × (1 / (1 + fit error)).
    raw: dict[int, float] = {}
    for item in fitted:
        if not item["common_pre_dates"] or item["pre_fit_error"] is None:
            item["status"] = "excluded"
            item["reason"] = "修复前没有与处理区共同的合格调查日，无法拟合权重。"
            continue
        scale = 1.0 / (1.0 + item["pre_fit_error"])
        raw[item["area_id"]] = max(1e-6, item["habitat_similarity"] * item["pre_date_coverage"] * scale)
    total = sum(raw.values())
    for item in fitted:
        if item["area_id"] not in raw:
            item["weight"] = 0.0
            continue
        weight = raw[item["area_id"]] / total if total else 0.0
        item["weight"] = round(weight, 4)
        if weight < min_share:
            item["status"] = "excluded"
            item["reason"] = f"修复前拟合权重 {weight:.1%} 低于最小贡献门槛 {min_share:.0%}，排除以免用噪声凑数。"
            item["weight"] = 0.0
        else:
            item["status"] = "included"
            item["reason"] = ""
    included = [item for item in fitted if item.get("status") == "included"]
    renormal = sum(item["weight"] for item in included)
    if renormal:
        for item in included:
            item["weight"] = round(item["weight"] / renormal, 4)
    diagnostics = {
        "pre_dates": pre_dates,
        "included_count": len(included),
        "weight_sum": round(sum(item["weight"] for item in included), 4),
    }
    if len(included) >= 2 and renormal:
        shares = [item["weight"] for item in included]
        diagnostics["weight_spread_pct"] = round((max(shares) - min(shares)) * 100, 1)
        diagnostics["weight_stability"] = (
            "stable"
            if (max(shares) - min(shares)) * 100 <= float(criteria.get("max_weight_spread_pct", 60.0))
            else "unstable"
        )
    elif included:
        diagnostics["weight_spread_pct"] = 100.0
        diagnostics["weight_stability"] = "single_reference"
    else:
        diagnostics["weight_spread_pct"] = None
        diagnostics["weight_stability"] = "no_reference"
    return fitted, diagnostics


def _post_series(area_obs: dict[str, Any], post_dates: list[str]) -> dict[str, float]:
    return _rate_series(area_obs, post_dates)


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}/有效相机日"


def analyse_plan(
    db,
    plan: ComparisonPlan,
    *,
    method: str,
    actor: str,
    note: str = "",
    previous_analysis_id: int | None = None,
    input_revision: dict[str, Any] | None = None,
    plan_changed: bool = False,
) -> ComparisonAnalysis:
    criteria = plan_criteria(plan)
    criteria.setdefault("species_codes", plan.species_codes)
    project = db.get(EngineeringProject, plan.treatment_project_id) if plan.treatment_project_id else None
    areas = areas_for_plan(db, plan)
    role_by_area = {m.area_id: m.role for m in plan.members}
    treatment_area = next((a for a in areas if (role_by_area.get(a.id) or a.role) == "treatment"), None)
    if treatment_area is None:
        raise DomainError("比较方案缺少处理区（修复区）", 409)
    if project is None:
        raise DomainError("比较方案未绑定工程资料", 409)

    obs_by_area: dict[int, dict[str, Any]] = {}
    for area in areas:
        obs_by_area[area.id] = build_area_observation(
            db, area, plan, criteria, project.id, role=role_by_area.get(area.id) or area.role
        )
    treatment = obs_by_area[treatment_area.id]
    candidate_codes = {
        a.id: habitat_similarity(treatment_area, a) for a in areas if a.id != treatment_area.id
    }

    # The pre/post split follows the construction window that actually reaches the
    # treatment area. A phase of the same project that only spills into a reference
    # area must not move the treatment's own baseline boundary.
    # The engineering record's own actual dates define the pre/post boundary; phase
    # rows refine the spatial footprint, not the calendar. A corrected project date
    # therefore moves the boundary and the recompute explains that input revision.
    construction_start = project.actual_start
    construction_end = project.actual_end
    # A date cannot be both pre and post: the post boundary is never earlier than the
    # start. An inconsistent record is rejected on revision, and this guard keeps an
    # already-stored inconsistency from duplicating a survey day.
    post_boundary = construction_end
    if construction_start is not None and (
        post_boundary is None or post_boundary < construction_start
    ):
        post_boundary = construction_start

    def is_pre(day: str) -> bool:
        return construction_start is not None and date.fromisoformat(day) < construction_start

    def is_post(day: str) -> bool:
        return post_boundary is not None and date.fromisoformat(day) > post_boundary

    treatment_usable = _date_sets(treatment)
    pre_dates = [d for d in treatment_usable if is_pre(d)]
    post_dates = [d for d in treatment_usable if is_post(d)]
    # A survey day that falls inside the construction period is neither baseline nor
    # outcome; it is reported as excluded rather than silently dropped.
    construction_period_dates = [
        d for d in treatment_usable if not is_pre(d) and not is_post(d)
    ]

    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for area in areas:
        if area.id == treatment_area.id:
            continue
        obs = obs_by_area[area.id]
        usable = _date_sets(obs)
        construction_dates: list[dict[str, Any]] = []
        for row in obs["units"]:
            if row["exclusion"] is not None:
                continue
            for window in obs["construction_windows"]:
                if window.get("project_id") == project.id:
                    continue
                start = window.get("start")
                end = window.get("end")
                if start and row["date"] >= start and (end is None or row["date"] <= end):
                    construction_dates.append(
                        {
                            "unit_id": row["unit_id"],
                            "date": row["date"],
                            "certificate_no": row["certificate_no"],
                            "impact_window": window,
                            "detail": (
                                f"参照区在 {start} 开工，{row['date']} 落在其施工窗口内，"
                                "该日不再作为修复后参照观测。"
                            ),
                        }
                    )
                    break
        candidate = {
            "area_id": area.id,
            "code": area.code,
            "name": area.name,
            "station_codes": obs["station_codes"],
            "habitat_similarity": candidate_codes[area.id],
            "pre_dates": [d for d in usable if is_pre(d)],
            "post_dates": [d for d in usable if is_post(d)],
            "missing_dates": obs["missing_dates"],
            "impacted_units": [
                {
                    "unit_id": r["unit_id"],
                    "date": r["date"],
                    "certificate_no": r["certificate_no"],
                    "impact_window": r["impact_window"],
                    "detail": r["exclusion_detail"],
                }
                for r in obs["units"]
                if r["exclusion"] == "construction_impact"
            ],
            "construction_dates": construction_dates,
            "other_excluded": [
                {"unit_id": r["unit_id"], "date": r["date"], "exclusion": r["exclusion"], "detail": r["exclusion_detail"]}
                for r in obs["units"]
                if r["exclusion"] not in {None, "construction_impact"}
            ],
        }
        candidates.append(candidate)

    eligible: list[dict[str, Any]] = []
    impact_threshold = float(criteria.get("data_impact_threshold_pct", 33.0)) / 100.0
    for cand in candidates:
        reason = None
        cand["total_units"] = len(obs_by_area[cand["area_id"]]["units"])
        cand["impacted_ratio"] = (
            round(len(cand["impacted_units"]) / cand["total_units"], 3)
            if cand["total_units"]
            else 0.0
        )
        in_window = {u["date"] for u in cand["construction_dates"]}
        cand["usable_post_dates"] = [d for d in cand["post_dates"] if d not in in_window]
        if cand["impacted_units"]:
            dates = sorted({u["date"] for u in cand["impacted_units"]})
            cand["impact_note"] = (
                f"{len(cand['impacted_units'])}/{cand['total_units']} 个调查日"
                f"（{'、'.join(dates)}）落在施工实际影响范围内（按实际边界，不按行政归属），"
                "这些单元从比较中剔除，其余调查日按原方案继续使用。"
            )
            if cand["impacted_ratio"] >= impact_threshold:
                cand["heavy_impact"] = (
                    f"受影响比例 {cand['impacted_ratio']:.0%} 达到 {impact_threshold:.0%}，"
                    "该参照仅剩少量合格单元，结论解释时需特别说明。"
                )
        if cand["construction_dates"] and not cand["impacted_units"]:
            # The reference's own construction starts mid-season: its pre-start
            # dates stay usable (weight fit), its post-start dates do not.
            cand["construction_note"] = (
                f"参照区在观察季中开工：{len(cand['construction_dates'])} 个修复后调查日落在其施工窗口内，"
                "仅开工前符合窗口的部分保留。"
            )
        if len(cand["pre_dates"]) < int(criteria["min_pre_dates_per_area"]):
            reason = (
                f"修复前合格调查仅 {len(cand['pre_dates'])} 个，低于最低 {criteria['min_pre_dates_per_area']} 个"
                "（可能因部分季节调查缺失或参照区自身开工）。"
            )
        if reason:
            exclusions.append(
                {
                    "area_id": cand["area_id"],
                    "code": cand["code"],
                    "name": cand["name"],
                    "reason": reason,
                    "impacted_unit_ids": [u["unit_id"] for u in cand["impacted_units"]],
                }
            )
        else:
            eligible.append(cand)

    common_pre = sorted(
        set(pre_dates).intersection(*[set(c["pre_dates"]) for c in eligible])
    ) if eligible else []
    enough_pre = (
        len(common_pre) >= int(criteria["min_common_pre_dates"])
        and len(pre_dates) >= int(criteria["min_pre_dates_per_area"])
    )
    enough_post = len(post_dates) >= int(criteria["min_post_dates_per_area"])

    weights: list[dict[str, Any]] = []
    for cand in candidates:
        if all(c["area_id"] != cand["area_id"] for c in eligible):
            weights.append(
                {
                    "area_id": cand["area_id"],
                    "code": cand["code"],
                    "name": cand["name"],
                    "common_pre_dates": [],
                    "pre_date_coverage": 0.0,
                    "pre_fit_error": None,
                    "habitat_similarity": cand["habitat_similarity"],
                    "series": {"pre": {}, "candidate": {}},
                    "status": "excluded",
                    "weight": 0.0,
                    "reason": next(
                        (e["reason"] for e in exclusions if e["area_id"] == cand["area_id"]),
                        "不满足纳入门槛。",
                    ),
                }
            )
    diagnostics: dict[str, Any] = {}
    status = "computed"
    reasons: list[str] = []
    review_reasons: list[str] = []
    if method == METHOD_DESCRIPTIVE:
        reasons.append("按方案指定只报告描述性差异，不拟合参照权重。")
        enough_pre = False
    if enough_pre and eligible:
        fitted, diagnostics = _fit_weights(
        treatment, [obs_by_area[c["area_id"]] | {"habitat_similarity": c["habitat_similarity"]} for c in eligible],
        common_pre, criteria,
    )
        if method == METHOD_SINGLE:
            # Single-reference plans keep exactly one reference: simplest to explain
            # and audit, but the whole comparison depends on it staying valid.
            usable = [w for w in fitted if w.get("status") == "included"]
            best = min(
                usable,
                key=lambda w: (w["pre_fit_error"] if w["pre_fit_error"] is not None else 9.9, w["area_id"]),
            ) if usable else None
            for item in fitted:
                if best is not None and item["area_id"] == best["area_id"]:
                    item["status"] = "included"
                    item["weight"] = 1.0
                else:
                    item["status"] = "excluded"
                    item["reason"] = (
                        "单参照方案：仅保留修复前拟合误差最小的参照，其余候选不进入比较。"
                        if best is not None
                        else item.get("reason") or "单参照方案未选出可用参照。"
                    )
                    item["weight"] = 0.0
            diagnostics["method_note"] = "单参照方案：解释直观但参照失效时整段比较中断，方案锁定后不自动替换。"
            diagnostics["weight_stability"] = "single_reference" if best else "no_reference"
            diagnostics["included_count"] = 1 if best else 0
            diagnostics["weight_sum"] = 1.0 if best else 0.0
        weights = fitted + weights
    if not enough_pre:
        status = METHOD_DESCRIPTIVE
        reasons.append(
            f"修复前共同合格调查日 {len(common_pre)} 个，低于最低 {criteria['min_common_pre_dates']} 个；"
            "只报告描述性差异，不强行拟合权重。"
        )
    if not enough_post:
        status = METHOD_DESCRIPTIVE if status == METHOD_DESCRIPTIVE else "descriptive_only"
        reasons.append(
            f"修复完成后的合格调查日 {len(post_dates)} 个，低于最低 {criteria['min_post_dates_per_area']} 个；"
            "当前只能给出修复前基线，不能报告修复后差异。"
        )

    included = [w for w in weights if w.get("status") == "included"]
    if status == "computed" and len(included) == 0:
        status = METHOD_DESCRIPTIVE
        reasons.append("没有合格参照区的修复前拟合权重，降级为描述性差异。")
    if status == "computed" and len(included) < 2 and method == METHOD_WEIGHTED:
        reasons.append("仅 1 个参照区进入组合，组合权重退化为单一参照。")

    # A reference whose construction starts inside the observation season keeps only
    # the pre-construction part of the window. If that leaves no usable post dates it
    # can no longer contribute to the post-restoration combination.
    post_participants: list[dict[str, Any]] = []
    post_dropped: list[dict[str, Any]] = []
    for item in included:
        obs = obs_by_area[item["area_id"]]
        cand = next((c for c in candidates if c["area_id"] == item["area_id"]), None)
        allowed_post = (cand or {}).get("usable_post_dates", post_dates)
        usable_post = _post_series(obs, allowed_post)
        item["post_dates"] = sorted(usable_post)
        if usable_post:
            post_participants.append(item)
        else:
            item["post_dates"] = []
            post_dropped.append(
                {
                    "area_id": item["area_id"],
                    "code": item["code"],
                    "name": item["name"],
                    "weight": item["weight"],
                    "reason": (
                        "参照区在观察季中开工：修复前部分保留并参与权重拟合，"
                        "修复后调查日全部落在施工实际影响范围内，无法进入修复后比较。"
                    ),
                }
            )
    diagnostics["post_participants"] = [item["code"] for item in post_participants]
    diagnostics["post_dropped"] = [item["code"] for item in post_dropped]
    min_post_refs = int(criteria.get("min_post_references", 2))
    post_loss = len(included) - len(post_participants)
    if status == "computed" and post_dropped:
        review_reasons.append(
            f"{'、'.join(item['code'] for item in post_dropped)} 修复后调查日受同期施工影响，"
            "仅保留修复前部分；已有比较的相关结论转为待复核。"
        )
    if status == "computed" and post_loss:
        review_reasons.append(
            f"{post_loss} 个已纳入参照的修复后调查日受同期施工影响而退出比较；"
            "依赖这些参照的修复后差异暂停，修复前部分仍保留。"
        )
    # Weight retained per post-restoration date. A day where the references that
    # survived construction carry too little of the pre-fitted weight cannot keep
    # its difference: the combination was fitted on evidence that no longer exists.
    min_retained = float(criteria.get("min_retained_weight_share", 0.6))
    retained_by_date: dict[str, float] = {}
    for day in post_dates:
        covered = 0.0
        for item in post_participants:
            if day in item["post_dates"]:
                covered += item["weight"]
        retained_by_date[day] = round(covered, 4)
    weakest_day = min(retained_by_date.items(), key=lambda kv: kv[1]) if retained_by_date else None
    diagnostics["min_retained_weight_share"] = min_retained
    diagnostics["retained_weight_by_date"] = retained_by_date
    diagnostics["retained_weight_share"] = weakest_day[1] if weakest_day else None
    insufficient_days = [day for day, share in retained_by_date.items() if share < min_retained]
    diagnostics["insufficient_post_dates"] = insufficient_days
    if (
        status == "computed"
        and len(post_participants) < min_post_refs
        and method != METHOD_SINGLE
    ):
        review_reasons.append(
            f"修复后可用参照仅 {len(post_participants)} 个，低于最低 {min_post_refs} 个，"
            "剩余证据不足以维持加权比较。"
        )
        status = "under_review"
    elif status == "computed" and insufficient_days:
        review_reasons.append(
            f"修复后日期 {'、'.join(insufficient_days)} 上仍可用的参照只承载原拟合权重的 "
            f"{weakest_day[1]:.0%}，低于 {min_retained:.0%}，这些差异不能按原方案报告。"
            + (
                "单参照方案的唯一参照在观察季开工，整段修复后比较随之中断。"
                if method == METHOD_SINGLE
                else ""
            )
        )
        status = "under_review"

    # Weighted post-restoration comparison, only with pre-fitted weights.
    results: list[dict[str, Any]] = []
    if status in {"computed", "under_review"} and post_participants:
        cand_post: dict[str, dict[str, float]] = {}
        for item in post_participants:
            obs = obs_by_area[item["area_id"]]
            cand_post[item["code"]] = _post_series(obs, item["post_dates"])
        treatment_post = _post_series(treatment, post_dates)
        for day in post_dates:
            # Weight per date, ignoring references missing that date.
            retained = retained_by_date.get(day, 0.0)
            insufficient = status != METHOD_DESCRIPTIVE and retained < min_retained
            num = 0.0
            den = 0.0
            contributions = []
            for item in post_participants:
                value = cand_post[item["code"]].get(day)
                if value is None:
                    contributions.append(
                        {
                            "area_id": item["area_id"],
                            "code": item["code"],
                            "weight": item["weight"],
                            "rate": None,
                            "included": False,
                            "reason": "该日缺少合格参照调查",
                        }
                    )
                    continue
                num += item["weight"] * value
                den += item["weight"]
                contributions.append(
                    {
                        "area_id": item["area_id"],
                        "code": item["code"],
                        "weight": item["weight"],
                        "rate": round(value, 4),
                        "included": True,
                        "contribution": round(item["weight"] * value, 4),
                    }
                )
            reference_rate = num / den if den else None
            treatment_rate = treatment_post.get(day)
            diff = (
                round(treatment_rate - reference_rate, 4)
                if not insufficient
                and treatment_rate is not None
                and reference_rate is not None
                else None
            )
            results.append(
                {
                    "date": day,
                    "phase": "post",
                    "retained_weight": retained,
                    "sufficient": not insufficient,
                    "note": (
                        f"该日仍可用参照只承载原拟合权重 {retained:.0%}，低于 {min_retained:.0%}，"
                        "差异暂停报告。"
                        if insufficient
                        else ""
                    ),
                    "treatment_rate": round(treatment_rate, 4) if treatment_rate is not None else None,
                    "reference_rate": round(reference_rate, 4) if reference_rate is not None else None,
                    "difference": diff,
                    "relative_change_pct": (
                        round(diff / reference_rate * 100, 1)
                        if diff is not None and reference_rate
                        else None
                    ),
                    "contributions": contributions,
                    "treatment_unit_id": _usable_by_date(treatment)[day]["unit_id"] if day in _usable_by_date(treatment) else None,
                }
            )

    # Sensitivity: leave-one-reference-out and pre-fit residuals and held-out pre date.
    sensitivity: dict[str, Any] = {}
    if post_participants and enough_post:
        loo = []
        full_rates = {r["date"]: r["reference_rate"] for r in results if r["reference_rate"] is not None}
        for item in post_participants:
            rest = [w for w in included if w["area_id"] != item["area_id"]]
            if not rest:
                continue
            rest_sum = sum(w["weight"] for w in rest)
            per_date = []
            for day in post_dates:
                if retained_by_date.get(day, 0.0) < min_retained:
                    per_date.append({"date": day, "reference_rate": None, "delta_pct": None})
                    continue
                rest_rates = {
                    w["code"]: _post_series(obs_by_area[w["area_id"]], [day]).get(day)
                    for w in rest
                }
                num = sum(w["weight"] * rest_rates[w["code"]] for w in rest if rest_rates.get(w["code"]) is not None)
                den = sum(w["weight"] for w in rest if rest_rates.get(w["code"]) is not None)
                per_date.append(
                    {
                        "date": day,
                        "reference_rate": round(num / den, 4) if den else None,
                        "delta_pct": (
                            round((num / den - full_rates[day]) / full_rates[day] * 100, 1)
                            if den and full_rates.get(day)
                            else None
                        ),
                    }
                )
            max_delta = max((abs(p["delta_pct"]) for p in per_date if p["delta_pct"] is not None), default=0.0)
            loo.append(
                {
                    "area_id": item["area_id"],
                    "code": item["code"],
                    "weight": item["weight"],
                    "redistributed_weight": round(
                        (item["weight"] / rest_sum) if rest_sum else 0.0, 4
                    ),
                    "per_date": per_date,
                    "max_delta_pct": max_delta,
                    "stable": max_delta <= float(criteria.get("leave_one_out_tolerance_pct", 15.0)),
                }
            )
        sensitivity["leave_one_out"] = loo
        # Held-out pre-restoration date: fit on fewer pre dates, compare the holdout.
        held = []
        if len(common_pre) >= 3:
            for hold in common_pre:
                print_dates = [d for d in common_pre if d != hold]
                refs = []
                for item in included:
                    t = _rate_series(treatment, print_dates)
                    c = _rate_series(obs_by_area[item["area_id"]], print_dates)
                    if not print_dates:
                        continue
                    err = sum(abs(t[d] - c[d]) for d in print_dates) / len(print_dates)
                    refs.append((item, err))
                if not refs:
                    continue
                best = min(refs, key=lambda pair: pair[1])
                obs_hold = _usable_by_date(obs_by_area[best[0]["area_id"]]).get(hold)
                treat_hold = _usable_by_date(treatment).get(hold)
                held.append(
                    {
                        "held_out_date": hold,
                        "fit_dates": print_dates,
                        "reference_code": best[0]["code"],
                        "fit_error": round(best[1], 4),
                        "observed_difference": (
                            round(treat_hold["observation_rate"] - obs_hold["observation_rate"], 4)
                            if treat_hold and obs_hold
                            else None
                        ),
                    }
                )
        sensitivity["held_out_pre_dates"] = held
        sensitivity["pre_fit_errors"] = [
            {
                "area_id": w["area_id"],
                "code": w["code"],
                "weight": w["weight"],
                "pre_fit_error": w["pre_fit_error"],
                "pre_date_coverage": w["pre_date_coverage"],
                "common_pre_dates": w["common_pre_dates"],
            }
            for w in weights
            if w.get("status") == "included"
        ]

    # Pre-restoration baseline difference (for context, never used to fit post).
    pre_results = []
    if common_pre:
        for day in common_pre:
            treat_row = _usable_by_date(treatment)[day]
            contributions = []
            num = 0.0
            den = 0.0
            for item in included:
                row = _usable_by_date(obs_by_area[item["area_id"]]).get(day)
                if row is None:
                    continue
                num += item["weight"] * row["observation_rate"]
                den += item["weight"]
                contributions.append(
                    {
                        "code": item["code"],
                        "weight": item["weight"],
                        "rate": row["observation_rate"],
                    }
                )
            ref_rate = num / den if den else None
            pre_results.append(
                {
                    "date": day,
                    "phase": "pre",
                    "treatment_rate": treat_row["observation_rate"],
                    "reference_rate": round(ref_rate, 4) if ref_rate is not None else None,
                    "difference": (
                        round(treat_row["observation_rate"] - ref_rate, 4)
                        if ref_rate is not None
                        else None
                    ),
                    "contributions": contributions,
                    "treatment_unit_id": treat_row["unit_id"],
                }
            )

    # Descriptive differences for every candidate area, independent of the fit.
    descriptive = []
    for area in areas:
        obs = obs_by_area[area.id]
        rows = [r for r in obs["units"] if r["exclusion"] is None]
        rates = [r["observation_rate"] for r in rows]
        descriptive.append(
            {
                "area_id": area.id,
                "code": area.code,
                "name": area.name,
                "role": role_by_area.get(area.id) or area.role,
                "usable_dates": [r["date"] for r in rows],
                "unit_count": len(rows),
                "mean_rate": round(sum(rates) / len(rates), 4) if rates else None,
                "detections": sum(r["detections"] for r in rows),
                "calibrated_effort_days": round(sum(r["effective_effort"]["effective_effort_days"] for r in rows), 3),
                "excluded_dates": [
                    {"date": r["date"], "exclusion": r["exclusion"], "detail": r["exclusion_detail"]}
                    for r in obs["units"]
                    if r["exclusion"] is not None
                ],
            }
        )

    # Pre-summary block requested by researchers.
    pre_summary = {
        "construction_start": _iso(construction_start),
        "construction_end": _iso(post_boundary),
        "common_pre_dates": common_pre,
        "treatment_pre_dates": pre_dates,
        "construction_period_dates": construction_period_dates,
        "construction_period_note": (
            "施工期内的调查日既不是修复前基线，也不是修复后结果，"
            "从比较中排除并在此列出。"
        ),
        "post_dates": post_dates,
        "pre_results": pre_results,
        "mean_pre_difference": (
            round(
                sum(r["difference"] for r in pre_results if r["difference"] is not None)
                / max(1, len([r for r in pre_results if r["difference"] is not None])),
                4,
            )
            if pre_results
            else None
        ),
    }

    post_summary = None
    if status == METHOD_WEIGHTED or (status != METHOD_DESCRIPTIVE and results):
        diffs = [r["difference"] for r in results if r["difference"] is not None and r.get("sufficient", True)]
        post_summary = {
            "post_dates": post_dates,
            "mean_difference": round(sum(diffs) / len(diffs), 4) if diffs else None,
            "mean_relative_change_pct": (
                round(
                    sum(
                        r["relative_change_pct"]
                        for r in results
                        if r["relative_change_pct"] is not None and r.get("sufficient", True)
                    )
                    / max(
                        1,
                        len(
                            [
                                r
                                for r in results
                                if r["relative_change_pct"] is not None and r.get("sufficient", True)
                            ]
                        ),
                    ),
                    1,
                )
                if results
                else None
            ),
            "reported_dates": [r["date"] for r in results if r.get("sufficient", True)],
            "paused_dates": [r["date"] for r in results if not r.get("sufficient", True)],
        }

    conclusions = _build_conclusions(
        status=status,
        reasons=reasons + review_reasons,
        treatment=treatment,
        candidates=candidates,
        included=included,
        post_participants=post_participants,
        post_dropped=post_dropped,
        diagnostics=diagnostics,
        post_summary=post_summary,
        pre_summary=pre_summary,
        criteria=criteria,
        sensitivity=sensitivity,
    )

    input_snapshot = {
        "areas": [
            {
                "area_id": obs["area_id"],
                "code": obs["code"],
                "role": obs["role"],
                "station_ids": obs["station_ids"],
                "units": [
                    {
                        "unit_id": r["unit_id"],
                        "date": r["date"],
                        "certificate_no": r["certificate_no"],
                        "certificate_status": r["certificate_status"],
                        "exclusion": r["exclusion"],
                        "detections": r["detections"],
                        "effective_effort_days": r["effective_effort"]["effective_effort_days"],
                        "calibration_id": r["effective_effort"]["calibration_id"],
                        "availability_notes": r["availability_notes"],
                        "observation_rate": r["observation_rate"],
                        "material_record_ids": r["material_record_ids"],
                        "evidence_record_ids": r["evidence_record_ids"],
                    }
                    for r in obs["units"]
                ],
                "missing_dates": obs["missing_dates"],
                "construction_windows": obs["construction_windows"],
            }
            for obs in obs_by_area.values()
        ],
        "project": {
            "project_id": project.id,
            "code": project.code,
            "revision": project.revision,
            "actual_start": _iso(project.actual_start),
            "actual_end": _iso(project.actual_end),
            "impact_boundary_hash": canonical_hash(project.impact_boundary or project.spatial_boundary)[:16],
            "phases": [
                {
                    "phase_id": p.id,
                    "phase_code": p.phase_code,
                    "actual_start": _iso(p.actual_start),
                    "actual_end": _iso(p.actual_end),
                    "revision": p.revision,
                }
                for p in project.phases
            ],
        },
        "criteria": criteria,
        "method": method,
    }
    batch = _input_batch(db, plan, project, areas, obs_by_area, criteria, input_snapshot)

    existing = db.execute(
        select(func.count()).select_from(ComparisonAnalysis).where(ComparisonAnalysis.plan_id == plan.id)
    ).scalar_one()
    code = f"CMP-{plan.plan_code}-{(existing + 1):02d}"
    payload = {
        "areas": [obs_by_area[a.id] for a in areas],
        "candidates": candidates,
        "weights": weights,
        "diagnostics": diagnostics,
        "conclusions": conclusions,
        "pre": pre_summary,
        "post": post_summary,
        "descriptive": descriptive,
        "sensitivity": sensitivity,
        "reasons": reasons + review_reasons,
        "review_reasons": review_reasons,
        "post_participants": [item["code"] for item in post_participants],
        "post_dropped": post_dropped,
        "common_pre_dates": common_pre,
        "post_dates": post_dates,
        "construction": {"start": _iso(construction_start), "end": _iso(construction_end)},
    }
    analysis = ComparisonAnalysis(
        analysis_code=code,
        plan_id=plan.id,
        plan_version=plan.version,
        status=status,
        method=method,
        summary={
            "treatment": {
                "area_id": treatment["area_id"],
                "code": treatment["code"],
                "name": treatment["name"],
            },
            "reasons": reasons + review_reasons,
            "review_reasons": review_reasons,
            "post_participants": [item["code"] for item in post_participants],
            "post_dropped": post_dropped,
            "diagnostics": diagnostics,
            "pre": pre_summary,
            "post": post_summary,
            "exclusions": exclusions,
            "conclusions": conclusions,
            "display": payload,
        },
        input_batch=batch,
        input_snapshot=input_snapshot,
        weights=weights,
        exclusions=exclusions,
        results=results,
        change_log=_change_entries(previous_analysis_id, input_revision, plan_changed),
        previous_analysis_id=previous_analysis_id,
        created_by=actor,
        note=note,
    )
    db.add(analysis)
    db.flush()
    db.add(
        AuditEvent(
            entity_type="comparison_analysis",
            entity_id=analysis.id,
            action="comparison.analysis_created",
            actor=actor,
            detail={
                "plan_id": plan.id,
                "status": analysis.status,
                "method": method,
                "included_references": [w["code"] for w in included],
                "previous_analysis_id": previous_analysis_id,
            },
        )
    )
    analysis._payload = payload  # type: ignore[attr-defined]
    return analysis


def _build_conclusions(
    *,
    status: str,
    reasons: list[str],
    treatment: dict[str, Any],
    candidates: list[dict[str, Any]],
    included: list[dict[str, Any]],
    post_participants: list[dict[str, Any]],
    post_dropped: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    post_summary: dict[str, Any] | None,
    pre_summary: dict[str, Any],
    criteria: dict[str, Any],
    sensitivity: dict[str, Any],
) -> list[dict[str, Any]]:
    """Turn the numbers into statements with an explicit non-applicable scope.

    No statement says the engineering work "worked"; each one names the compared
    units and the conditions under which it stops applying.
    """
    statements: list[dict[str, Any]] = []
    included_codes = [w["code"] for w in (post_participants or included)]
    impact_codes = [c["code"] for c in candidates if c["impacted_units"]]
    if status == "under_review":
        statements.append(
            {
                "key": "under_review",
                "verdict": "比较结论暂停：修复后可用参照不足",
                "basis": "；".join(reasons) or "参照在观察季开工，修复后证据不足。",
                "not_applicable": (
                    "暂停期间不给出工程影响判断；参照区的区域自身观测事实仍可查询，"
                    "补齐调查或修订工程范围后可重新计算并生成新报告。"
                ),
            }
        )
    elif status == METHOD_DESCRIPTIVE:
        statements.append(
            {
                "key": "descriptive_only",
                "verdict": "描述性差异（尚不足以支持工程影响判断）",
                "basis": "；".join(reasons) or "合格修复前共同窗口不足。",
                "not_applicable": (
                    f"本结论只描述 {treatment['code']} 与候选参照在观测窗口内的差异，"
                    "不归因于湿地修复工程；不得作为工程有效性判断或项目评优依据。"
                ),
            }
        )
    else:
        mean_diff = (post_summary or {}).get("mean_difference")
        direction = "高于" if (mean_diff or 0) > 0 else "低于" if (mean_diff or 0) < 0 else "接近"
        statements.append(
            {
                "key": "post_difference",
                "verdict": (
                    f"修复后 {treatment['code']} 的校准观测率平均{direction}参照组合"
                    f"{abs(mean_diff):.3f}/有效相机日"
                    if mean_diff is not None
                    else "修复后差异无法计算"
                ),
                "basis": (
                    f"权重仅由修复前共同窗口 {', '.join(pre_summary['common_pre_dates']) or '无'} 拟合；"
                    f"修复后比较日 {', '.join((post_summary or {}).get('post_dates', [])) or '无'}。"
                ),
                "not_applicable": (
                    f"仅适用于参照组合（{', '.join(included_codes)}）与当前观测窗口；"
                    f"若参照区施工影响扩大、工程实际日期更正或调查证书状态改变，该结论需复核。"
                ),
            }
        )
    if diagnostics.get("weight_stability") == "unstable":
        statements.append(
            {
                "key": "weight_instability",
                "verdict": "参照权重差异较大，组合结果对单个参照敏感",
                "basis": f"权重极差 {diagnostics.get('weight_spread_pct')}% 超过说明门槛。",
                "not_applicable": "权重不稳定的组合不适合单独作为工程影响结论，应同时查看去一参照敏感性。",
            }
        )
    for dropped in post_dropped:
        statements.append(
            {
                "key": f"post_dropped_{dropped['code']}",
                "verdict": f"{dropped['code']} 在观察季开工，修复后部分不再参与比较",
                "basis": dropped["reason"],
                "not_applicable": (
                    f"{dropped['code']} 的修复前证据仍用于权重拟合；若其剩余修复前窗口不足，"
                    "整段比较需复核。"
                ),
            }
        )
    if impact_codes:
        statements.append(
            {
                "key": "impact_exclusion",
                "verdict": f"{'、'.join(impact_codes)} 的部分调查受同期施工波及，已从比较剔除",
                "basis": "按工程资料的实际影响边界与施工日期判定，不按样区行政归属。",
                "not_applicable": "被剔除区域的区域自身观测事实仍可查看，但不得进入工程影响比较。",
            }
        )
    if sensitivity.get("leave_one_out") and any(
        not row["stable"] for row in sensitivity["leave_one_out"]
    ):
        unstable = [row["code"] for row in sensitivity["leave_one_out"] if not row["stable"]]
        statements.append(
            {
                "key": "leave_one_out",
                "verdict": f"去掉 {'、'.join(unstable)} 后参照率变化超过容忍度",
                "basis": f"容忍度 {criteria.get('leave_one_out_tolerance_pct')}%。",
                "not_applicable": "该组合对单一参照依赖偏高，结论仅在与全部参照同时存在时成立。",
            }
        )
    statements.append(
        {
            "key": "effort_note",
            "verdict": "观测率已按有效努力量校准",
            "basis": "每个调查单元先按可用相机小时×判读完成比例计算有效努力量，再计算单位努力观测率。",
            "not_applicable": "照片总数不用于比较；若判读完成比例变化，同一区域前后也不可直接比较。",
        }
    )
    return statements


def _change_entries(
    previous_analysis_id: int | None,
    input_revision: dict[str, Any] | None,
    plan_changed: bool,
) -> list[dict[str, Any]]:
    if previous_analysis_id is None and not input_revision:
        return [
            {
                "source": "initial",
                "summary": "首次计算：按当前方案与输入批次生成基线结果。",
            }
        ]
    entries: list[dict[str, Any]] = []
    if input_revision:
        entries.append(
            {
                "source": "input_revision",
                "summary": input_revision.get("summary")
                or "工程输入修订，可比单元、权重与差异随之重新计算。",
                "detail": input_revision,
            }
        )
    if plan_changed:
        entries.append(
            {
                "source": "plan_change",
                "summary": "比较方案变更（成员、观察窗口或纳入依据），按新方案重新计算。",
            }
        )
    return entries


def diff_analyses(previous: ComparisonAnalysis | None, current: ComparisonAnalysis) -> dict[str, Any]:
    """Explain why conclusions moved: input revision vs plan change, kept separate."""
    if previous is None:
        return {
            "source": "initial",
            "summary": "首次计算。",
            "units": [],
            "weights": [],
            "differences": [],
        }
    prev_batch = previous.input_batch or {}
    cur_batch = current.input_batch or {}
    prev_included = {row["unit_key"] for row in prev_batch.get("units", []) if row.get("included")}
    cur_included = {row["unit_key"] for row in cur_batch.get("units", []) if row.get("included")}
    all_units = {row["unit_key"] for row in prev_batch.get("units", [])} | {
        row["unit_key"] for row in cur_batch.get("units", [])
    }
    added_units = sorted(cur_included - prev_included)
    removed_units = sorted(prev_included - cur_included)
    unchanged_excluded = sorted(all_units - prev_included - cur_included)
    prev_weights = {w["code"]: w.get("weight", 0.0) for w in (previous.weights or []) if w.get("status") == "included"}
    cur_weights = {w["code"]: w.get("weight", 0.0) for w in (current.weights or []) if w.get("status") == "included"}
    weight_changes = [
        {
            "code": code,
            "previous": prev_weights.get(code),
            "current": cur_weights.get(code),
            "delta": round((cur_weights.get(code) or 0.0) - (prev_weights.get(code) or 0.0), 4),
        }
        for code in sorted(set(prev_weights) | set(cur_weights))
        if abs((cur_weights.get(code) or 0.0) - (prev_weights.get(code) or 0.0)) > 1e-9
    ]
    prev_diff = {r["date"]: r.get("difference") for r in (previous.results or [])}
    cur_diff = {r["date"]: r.get("difference") for r in (current.results or [])}
    difference_rows = [
        {
            "date": day,
            "previous_difference": prev_diff.get(day),
            "current_difference": cur_diff.get(day),
            "delta": (
                round((cur_diff.get(day) or 0.0) - (prev_diff.get(day) or 0.0), 4)
                if prev_diff.get(day) is not None and cur_diff.get(day) is not None
                else None
            ),
            "reason": "对应调查单元的可比状态或证据输入改变" if day in cur_diff else "该日已不进入比较",
        }
        for day in sorted(set(prev_diff) | set(cur_diff))
        if prev_diff.get(day) != cur_diff.get(day)
    ]
    engineering_changed = (prev_batch.get("engineering") or {}).get("fingerprint") != (
        cur_batch.get("engineering") or {}
    ).get("fingerprint")
    survey_changed = (prev_batch.get("survey_fingerprint") or "") != (
        cur_batch.get("survey_fingerprint") or ""
    )
    input_changed = engineering_changed or survey_changed
    plan_changed = (prev_batch.get("plan_signature") or "") != (cur_batch.get("plan_signature") or "")
    if input_changed and plan_changed:
        source = "input_revision_and_plan_change"
    elif engineering_changed:
        source = "engineering_input_revision"
    elif survey_changed:
        source = "survey_input_revision"
    elif plan_changed:
        source = "plan_change"
    else:
        source = "recompute_same_inputs"
    return {
        "source": source,
        "previous_analysis_id": previous.id,
        "previous_analysis_code": previous.analysis_code,
        "engineering_revision": {
            "previous": (prev_batch.get("engineering") or {}),
            "current": (cur_batch.get("engineering") or {}),
        },
        "plan_signature": {
            "previous": prev_batch.get("plan_signature"),
            "current": cur_batch.get("plan_signature"),
        },
        "engineering_input_changed": engineering_changed,
        "survey_input_changed": survey_changed,
        "units_added": added_units,
        "units_removed": removed_units,
        "units_still_excluded": unchanged_excluded,
        "weight_changes": weight_changes,
        "difference_changes": difference_rows,
        "explanation": (
            "输入修订（工程日期/影响边界/补录记录）与方案变更分列记录；"
            "差异变化可逐项追到具体调查单元，不合并成一次工程效果变化。"
        ),
    }


def _input_batch(
    db,
    plan: ComparisonPlan,
    project: EngineeringProject,
    areas: list[ComparisonArea],
    obs_by_area: dict[int, dict[str, Any]],
    criteria: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    units = []
    for obs in obs_by_area.values():
        for row in obs["units"]:
            units.append(
                {
                    "unit_key": f"{obs['code']}:{row['date']}",
                    "area_code": obs["code"],
                    "station_id": row["station_id"],
                    "unit_id": row["unit_id"],
                    "date": row["date"],
                    "certificate_no": row["certificate_no"],
                    "certificate_status": row["certificate_status"],
                    "included": row["exclusion"] is None,
                    "exclusion": row["exclusion"],
                    "observation_rate": row["observation_rate"],
                    "impact_source_ref": (row.get("impact_window") or {}).get("source_ref"),
                }
            )
    # Engineering inputs are every project whose actual footprint or dates touch a
    # compared area — the treatment project plus any concurrent works. Revising any
    # of them must register as an input change, not as a plan change.
    touched_projects = {
        w["project_id"]
        for obs in obs_by_area.values()
        for w in obs.get("construction_windows", [])
    }
    touched_projects.add(project.id)
    project_rows = list(
        db.execute(
            select(EngineeringProject)
            .where(EngineeringProject.id.in_(sorted(touched_projects)))
            .order_by(EngineeringProject.id)
        ).scalars()
    ) if touched_projects else []
    engineering = {
        "project_id": project.id,
        "code": project.code,
        "revision": project.revision,
        "actual_start": _iso(project.actual_start),
        "actual_end": _iso(project.actual_end),
        "projects": [
            {
                "project_id": row.id,
                "code": row.code,
                "revision": row.revision,
                "actual_start": _iso(row.actual_start),
                "actual_end": _iso(row.actual_end),
                "phases": [
                    {
                        "phase_code": ph.phase_code,
                        "actual_start": _iso(ph.actual_start),
                        "actual_end": _iso(ph.actual_end),
                        "revision": ph.revision,
                    }
                    for ph in row.phases
                ],
            }
            for row in project_rows
        ],
        "fingerprint": canonical_hash(
            {
                "status": project.status,
                "spatial_boundary": project.spatial_boundary,
                "impact_boundary": project.impact_boundary,
                "actual_start": _iso(project.actual_start),
                "actual_end": _iso(project.actual_end),
                "phases": [
                    {
                        "phase_code": p.phase_code,
                        "spatial": p.boundary,
                        "actual_start": _iso(p.actual_start),
                        "actual_end": _iso(p.actual_end),
                        "revision": p.revision,
                    }
                    for p in project.phases
                ],
                "concurrent_projects": [
                    {
                        "code": row.code,
                        "revision": row.revision,
                        "impact_boundary": row.impact_boundary,
                        "actual_start": _iso(row.actual_start),
                        "actual_end": _iso(row.actual_end),
                        "phases": [
                            {
                                "phase_code": ph.phase_code,
                                "boundary": ph.boundary,
                                "actual_start": _iso(ph.actual_start),
                                "actual_end": _iso(ph.actual_end),
                            }
                            for ph in row.phases
                        ],
                    }
                    for row in project_rows
                    if row.id != project.id
                ],
            }
        )[:16],
    }
    plan_signature = canonical_hash(
        {
            "plan_code": plan.plan_code,
            "version": plan.version,
            "members": sorted(
                [{"area_id": m.area_id, "role": m.role} for m in plan.members],
                key=lambda x: x["area_id"],
            ),
            "observation_start": plan.observation_start.isoformat(),
            "observation_end": plan.observation_end.isoformat(),
            "season_scope": sorted(plan.season_scope or []),
            "species_codes": sorted(plan.species_codes or []),
            "criteria": criteria,
        }
    )[:16]
    survey_fingerprint = canonical_hash(
        {
            "units": sorted(
                [
                    {
                        "unit_key": row["unit_key"],
                        "certificate_no": row["certificate_no"],
                        "certificate_status": row["certificate_status"],
                        "included": row["included"],
                        "exclusion": row["exclusion"],
                        "observation_rate": row["observation_rate"],
                    }
                    for row in units
                ],
                key=lambda r: r["unit_key"],
            )
        }
    )[:16]
    return {
        "computed_at": utcnow().isoformat(),
        "survey_fingerprint": survey_fingerprint,
        "plan_code": plan.plan_code,
        "plan_version": plan.version,
        "plan_signature": plan_signature,
        "observation_window": [plan.observation_start.isoformat(), plan.observation_end.isoformat()],
        "season_scope": plan.season_scope,
        "species_codes": plan.species_codes,
        "engineering": engineering,
        "areas": [
            {
                "area_id": a.id,
                "code": a.code,
                "role": a.role,
                "station_ids": [link.station_id for link in a.stations],
                "boundary_hash": canonical_hash(a.boundary)[:16],
            }
            for a in areas
        ],
        "units": units,
        "criteria": criteria,
    }


# ----------------------------------------------------------------- recompute
def latest_analysis(db, plan_id: int) -> ComparisonAnalysis | None:
    return (
        db.execute(
            select(ComparisonAnalysis)
            .where(ComparisonAnalysis.plan_id == plan_id)
            .order_by(ComparisonAnalysis.id.desc())
        )
        .scalars()
        .first()
    )


def _included_codes(analysis: ComparisonAnalysis | None) -> set[str]:
    if analysis is None:
        return set()
    return {w["code"] for w in (analysis.weights or []) if w.get("status") == "included"}


def _included_unit_keys(analysis: ComparisonAnalysis | None) -> set[str]:
    if analysis is None:
        return set()
    engineering = analysis.input_batch or {}
    return {row["unit_key"] for row in engineering.get("units", []) if row.get("included")}


def recompute_plan(
    db,
    plan: ComparisonPlan,
    *,
    method: str | None = None,
    actor: str,
    note: str = "",
    input_revision: dict[str, Any] | None = None,
    plan_changed: bool = False,
) -> tuple[ComparisonAnalysis, dict[str, Any]]:
    """Recompute and explain movement; never overwrite the earlier report.

    If a reference that was previously part of the combination is now affected by
    construction, or its pre-restoration evidence is no longer sufficient, the new
    report is issued as ``under_review`` and the affected earlier conclusions are
    listed as paused — the earlier report keeps its own evidence.
    """
    previous = latest_analysis(db, plan.id)
    chosen = method or (previous.method if previous else METHOD_WEIGHTED)
    analysis = analyse_plan(
        db,
        plan,
        method=chosen,
        actor=actor,
        note=note,
        previous_analysis_id=previous.id if previous else None,
        input_revision=input_revision,
        plan_changed=plan_changed,
    )
    payload = getattr(analysis, "_payload", {}) or {}
    diff = diff_analyses(previous, analysis)
    paused: list[dict[str, Any]] = []

    prev_codes = _included_codes(previous)
    cur_codes = _included_codes(analysis)
    lost_codes = sorted(prev_codes - cur_codes)
    exits: list[dict[str, Any]] = []
    if previous is not None and lost_codes:
        exclusions = {e["code"]: e for e in payload.get("exclusions", [])}
        candidates = {c["code"]: c for c in payload.get("candidates", [])}
        dropped = {d["code"]: d for d in payload.get("post_dropped", [])}
        for code in lost_codes:
            exclusion = exclusions.get(code)
            cand = candidates.get(code, {})
            drop = dropped.get(code)
            affected_units = [
                unit["unit_id"] for unit in cand.get("impacted_units", [])
            ] or (exclusion.get("impacted_unit_ids", []) if exclusion else [])
            exits.append(
                {
                    "reference": code,
                    "reason": (
                        exclusion["reason"]
                        if exclusion
                        else drop["reason"]
                        if drop
                        else "修复前共同窗口或权重不再满足纳入门槛，参照退出组合。"
                    ),
                    "affected_unit_ids": affected_units,
                    "affected_dates": [u.get("date") for u in cand.get("impacted_units", [])]
                    or (cand.get("construction_dates") and [u["date"] for u in cand["construction_dates"]])
                    or [],
                }
            )
    # Pause only when the recompute can no longer sustain a comparison. A reference
    # exiting while enough others remain is a revision to explain, not a pause.
    if previous is not None and previous.status == "computed" and analysis.status != "computed":
        paused.append(
            {
                "reference": None,
                "reason": "；".join(payload.get("reasons", [])) or "合格证据不足。",
                "affected_unit_ids": sorted(_included_unit_keys(previous) - _included_unit_keys(analysis)),
                "paused_conclusions": [
                    c["key"] for c in (previous.summary or {}).get("conclusions", [])
                ]
                or ["post_difference"],
                "previous_analysis_code": previous.analysis_code,
            }
        )
    if paused:
        analysis.summary = {
            **(analysis.summary or {}),
            "paused": paused,
            "review_note": (
                f"剩余证据不足以维持比较，相关结论转为待复核；旧报告 "
                f"{previous.analysis_code if previous else '—'} 保留当时证据。"
            ),
        }
        db.add(
            AuditEvent(
                entity_type="comparison_analysis",
                entity_id=analysis.id,
                action="comparison.conclusions_paused",
                actor=actor,
                detail={"previous_analysis_id": previous.id if previous else None, "paused": paused},
            )
        )
    elif exits:
        analysis.summary = {
            **(analysis.summary or {}),
            "reference_exits": exits,
            "review_note": (
                "参照退出组合但剩余参照足以维持比较：结论按新输入重算，"
                "退出原因与受影响调查单元逐项列出。"
            ),
        }
    diff["paused"] = paused
    diff["reference_exits"] = exits
    diff["status"] = analysis.status
    db.flush()
    return analysis, diff


# ------------------------------------------------------------------ engineering
def _revision_row(
    db,
    *,
    project: EngineeringProject,
    phase: EngineeringPhase | None,
    field: str,
    previous: dict[str, Any],
    updated: dict[str, Any],
    reason: str,
    actor: str,
) -> EngineeringRevision:
    row = EngineeringRevision(
        project_id=project.id,
        phase_id=phase.id if phase else None,
        field=field,
        previous=previous,
        updated=updated,
        reason=reason,
        actor=actor,
    )
    db.add(row)
    db.flush()
    db.add(
        AuditEvent(
            entity_type="engineering_project",
            entity_id=project.id,
            action="engineering.revised",
            actor=actor,
            detail={"field": field, "phase_id": phase.id if phase else None, "reason": reason},
        )
    )
    return row


def revise_project(
    db,
    project: EngineeringProject,
    *,
    actual_start: date | None,
    actual_end: date | None,
    impact_boundary: dict[str, Any] | None,
    note: str,
    actor: str,
    field: str = "dates",
) -> list[EngineeringRevision]:
    revisions: list[EngineeringRevision] = []
    previous_start = project.actual_start
    previous_end = project.actual_end
    if actual_start is not None and actual_start != previous_start:
        project.actual_start = actual_start
        revisions.append(
            _revision_row(
                db,
                project=project,
                phase=None,
                field=f"{field}:actual_start",
                previous={"actual_start": _iso(previous_start)},
                updated={"actual_start": _iso(actual_start)},
                reason=note,
                actor=actor,
            )
        )
    if actual_end is not None and actual_end != previous_end:
        project.actual_end = actual_end
        revisions.append(
            _revision_row(
                db,
                project=project,
                phase=None,
                field=f"{field}:actual_end",
                previous={"actual_end": _iso(previous_end)},
                updated={"actual_end": _iso(actual_end)},
                reason=note,
                actor=actor,
            )
        )
    if (
        actual_start is not None
        and actual_end is not None
        and actual_end < actual_start
    ):
        raise DomainError("实际完工日期不能早于实际开工日期", 422)
    if impact_boundary is not None:
        previous_boundary = project.impact_boundary
        project.impact_boundary = impact_boundary
        revisions.append(
            _revision_row(
                db,
                project=project,
                phase=None,
                field=f"{field}:impact_boundary",
                previous={"impact_boundary": previous_boundary},
                updated={"impact_boundary": impact_boundary},
                reason=note,
                actor=actor,
            )
        )
    if revisions:
        project.revision += 1
        db.flush()
    return revisions


def add_construction_phase(
    db,
    project: EngineeringProject,
    *,
    phase_code: str,
    name: str,
    actual_start: date,
    actual_end: date | None,
    boundary: dict[str, Any],
    source_type: str,
    source_ref: str,
    note: str,
    actor: str,
    concurrent_code: str | None = None,
    concurrent_name: str | None = None,
) -> EngineeringProject:
    """Record a construction phase.

    With ``concurrent_code`` the phase belongs to a *separate* concurrent project
    (for example a restoration starting on a reference area). Keeping it separate
    means the treatment project's own dates still define the pre/post boundary.
    """
    if concurrent_code:
        concurrent = EngineeringProject(
            code=concurrent_code,
            name=concurrent_name or name,
            project_type="wetland_restoration",
            status="in_progress",
            spatial_boundary=boundary,
            impact_boundary=boundary,
            actual_start=actual_start,
            actual_end=actual_end,
            source_type=source_type,
            source_ref=source_ref,
            description=note,
            revision=1,
            created_by=actor,
        )
        db.add(concurrent)
        db.flush()
        project = concurrent
    phase = EngineeringPhase(
        project_id=project.id,
        phase_code=phase_code,
        name=name,
        stage_type="construction",
        planned_start=actual_start,
        planned_end=actual_end,
        actual_start=actual_start,
        actual_end=actual_end,
        boundary=boundary,
        source_type=source_type,
        source_ref=source_ref,
        revision=1,
        note=note,
    )
    db.add(phase)
    db.flush()
    project.revision += 1
    _revision_row(
        db,
        project=project,
        phase=phase,
        field="phase:added",
        previous={},
        updated={
            "phase_code": phase_code,
            "actual_start": _iso(actual_start),
            "actual_end": _iso(actual_end),
            "name": name,
        },
        reason=note,
        actor=actor,
    )
    db.flush()
    return project


def project_payload(db, project: EngineeringProject) -> dict[str, Any]:
    revisions = list(
        db.execute(
            select(EngineeringRevision)
            .where(EngineeringRevision.project_id == project.id)
            .order_by(EngineeringRevision.id.desc())
        ).scalars()
    )
    return {
        "id": project.id,
        "code": project.code,
        "name": project.name,
        "project_type": project.project_type,
        "status": project.status,
        "spatial_boundary": project.spatial_boundary,
        "impact_boundary": project.impact_boundary,
        "actual_start": _iso(project.actual_start),
        "actual_end": _iso(project.actual_end),
        "source_type": project.source_type,
        "source_ref": project.source_ref,
        "description": project.description,
        "revision": project.revision,
        "created_by": project.created_by,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "phases": [
            {
                "id": p.id,
                "phase_code": p.phase_code,
                "name": p.name,
                "stage_type": p.stage_type,
                "planned_start": _iso(p.planned_start),
                "planned_end": _iso(p.planned_end),
                "actual_start": _iso(p.actual_start),
                "actual_end": _iso(p.actual_end),
                "boundary": p.boundary,
                "source_type": p.source_type,
                "source_ref": p.source_ref,
                "revision": p.revision,
                "note": p.note,
            }
            for p in project.phases
        ],
        "revisions": [
            {
                "id": r.id,
                "phase_id": r.phase_id,
                "field": r.field,
                "previous": r.previous,
                "updated": r.updated,
                "reason": r.reason,
                "actor": r.actor,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in revisions
        ],
    }




# ----------------------------------------------------------------- payloads
def analysis_payload(db, analysis: ComparisonAnalysis, include_diff: bool = True) -> dict[str, Any]:
    payload = getattr(analysis, "_payload", None)
    plan = db.get(ComparisonPlan, analysis.plan_id)
    previous = (
        db.get(ComparisonAnalysis, analysis.previous_analysis_id)
        if analysis.previous_analysis_id
        else None
    )
    data = {
        "id": analysis.id,
        "analysis_code": analysis.analysis_code,
        "plan_id": analysis.plan_id,
        "plan_code": plan.plan_code if plan else None,
        "plan_version": analysis.plan_version,
        "status": analysis.status,
        "method": analysis.method,
        "method_label": {
            METHOD_WEIGHTED: "多参照加权比较",
            METHOD_SINGLE: "单参照比较",
            METHOD_DESCRIPTIVE: "描述性差异",
        }.get(analysis.method, analysis.method),
        "input_batch": analysis.input_batch,
        "input_snapshot": analysis.input_snapshot,
        "weights": analysis.weights,
        "exclusions": analysis.exclusions,
        "results": analysis.results,
        "summary": analysis.summary,
        "change_log": analysis.change_log,
        "previous_analysis_id": analysis.previous_analysis_id,
        "created_by": analysis.created_by,
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
        "note": analysis.note,
        "frozen": True,
    }
    if payload is None:
        # Older reports stored their display block inside summary; the frozen
        # report is never recomputed to fill a view.
        payload = (analysis.summary or {}).get("display", {})
    data.update(
        {
            "areas": payload.get("areas", []),
            "candidates": payload.get("candidates", []),
            "diagnostics": payload.get("diagnostics", (analysis.summary or {}).get("diagnostics", {})),
            "conclusions": payload.get(
                "conclusions", (analysis.summary or {}).get("conclusions", [])
            ),
            "pre": payload.get("pre", (analysis.summary or {}).get("pre", {})),
            "post": payload.get("post", (analysis.summary or {}).get("post", {})),
            "descriptive": payload.get("descriptive", []),
            "sensitivity": payload.get("sensitivity", {}),
            "reasons": payload.get("reasons", (analysis.summary or {}).get("reasons", [])),
        }
    )
    if include_diff:
        data["diff"] = diff_analyses(previous, analysis)
    return data


def area_payload(db, area: ComparisonArea) -> dict[str, Any]:
    stations = [
        s
        for s in db.execute(
            select(Station).where(Station.id.in_([link.station_id for link in area.stations]))
        ).scalars()
    ]
    return {
        "id": area.id,
        "code": area.code,
        "name": area.name,
        "zone_id": area.zone_id,
        "role": area.role,
        "role_label": "处理区（修复区）" if area.role == "treatment" else "候选参照区",
        "boundary": area.boundary,
        "centroid": {"lat": area.centroid_lat, "lon": area.centroid_lon},
        "elevation_range_m": [area.elevation_min_m, area.elevation_max_m],
        "habitat": area.habitat,
        "description": area.description,
        "status": area.status,
        "stations": [{"id": s.id, "code": s.code, "name": s.name} for s in stations],
    }


def plan_payload(db, plan: ComparisonPlan) -> dict[str, Any]:
    members = []
    for member in plan.members:
        area = db.get(ComparisonArea, member.area_id)
        members.append(
            {
                "id": member.id,
                "area_id": member.area_id,
                "area_code": area.code if area else None,
                "area_name": area.name if area else None,
                "role": member.role,
                "note": member.note,
            }
        )
    project = db.get(EngineeringProject, plan.treatment_project_id) if plan.treatment_project_id else None
    latest = latest_analysis(db, plan.id)
    return {
        "id": plan.id,
        "plan_code": plan.plan_code,
        "name": plan.name,
        "version": plan.version,
        "status": plan.status,
        "season_scope": plan.season_scope,
        "observation_start": plan.observation_start.isoformat(),
        "observation_end": plan.observation_end.isoformat(),
        "species_codes": plan.species_codes,
        "criteria": plan_criteria(plan),
        "treatment_project_id": plan.treatment_project_id,
        "treatment_project": {
            "id": project.id,
            "code": project.code,
            "name": project.name,
            "actual_start": _iso(project.actual_start),
            "actual_end": _iso(project.actual_end),
            "revision": project.revision,
        }
        if project
        else None,
        "created_by": plan.created_by,
        "locked_by": plan.locked_by,
        "locked_at": plan.locked_at.isoformat() if plan.locked_at else None,
        "note": plan.note,
        "members": members,
        "latest_analysis": (
            {
                "id": latest.id,
                "analysis_code": latest.analysis_code,
                "status": latest.status,
                "method": latest.method,
                "created_at": latest.created_at.isoformat() if latest.created_at else None,
            }
            if latest
            else None
        ),
    }


def plan_missing_reason(plan: ComparisonPlan) -> str | None:
    if plan.treatment_project_id is None:
        return "方案未绑定工程资料"
    if not any(m.role == "treatment" for m in plan.members):
        return "方案未指定处理区"
    return None


def area_observation_payload(db, area: ComparisonArea, plan: ComparisonPlan) -> dict[str, Any]:
    criteria = plan_criteria(plan)
    criteria["species_codes"] = plan.species_codes
    role = next((m.role for m in plan.members if m.area_id == area.id), area.role)
    obs = build_area_observation(
        db, area, plan, criteria, plan.treatment_project_id, role=role
    )
    return obs


def reference_effect(
    treatment_rate: float | None, original_reference_rate: float | None, new_reference_rate: float | None
) -> dict[str, Any]:
    """Explain how removing or re-weighting one reference moves the difference."""
    if treatment_rate is None or original_reference_rate is None or new_reference_rate is None:
        return {"applicable": False, "note": "缺少该日参照率，无法给出敏感性说明。"}
    original = round(treatment_rate - original_reference_rate, 4)
    revised = round(treatment_rate - new_reference_rate, 4)
    delta = round(revised - original, 4)
    return {
        "applicable": True,
        "original_difference": original,
        "revised_difference": revised,
        "delta": delta,
        "note": (
            "结论方向改变" if (original > 0) != (revised > 0) else "结论方向未变"
        )
        + f"；差异 {original:+.3f} → {revised:+.3f}（有效相机日）。",
    }
