from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    AuditEvent,
    ComparisonAnalysis,
    ComparisonArea,
    ComparisonPlan,
    EngineeringProject,
    SurveyAvailabilityNote,
    User,
)
from ..restoration import (
    DomainError,
    add_construction_phase,
    analysis_payload,
    area_observation_payload,
    area_payload,
    latest_analysis,
    plan_missing_reason,
    plan_payload,
    project_payload,
    recompute_plan,
    revise_project,
)
from ..schemas import (
    AvailabilityNoteIn,
    ComparisonAnalyseIn,
    ComparisonRecomputeIn,
    EngineeringDatesIn,
    EngineeringPhaseIn,
)
from ..security import get_current_user, require_roles

router = APIRouter(prefix="/comparison", tags=["comparison"])


def _plan(db: Session, plan_id: int) -> ComparisonPlan:
    plan = db.get(ComparisonPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "比较方案不存在")
    return plan


def _project(db: Session, project_id: int) -> EngineeringProject:
    project = db.get(EngineeringProject, project_id)
    if project is None:
        raise HTTPException(404, "工程资料不存在")
    return project


def _analysis(db: Session, analysis_id: int) -> ComparisonAnalysis:
    analysis = db.get(ComparisonAnalysis, analysis_id)
    if analysis is None:
        raise HTTPException(404, "分析结果不存在")
    return analysis


def _audit(db: Session, entity_type: str, entity_id: int, action: str, actor: str, detail: dict) -> None:
    db.add(
        AuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            detail=detail,
        )
    )


# ------------------------------------------------------------------ catalog
@router.get("/areas")
def list_areas(db: Session = Depends(get_db), _=Depends(get_current_user)):
    areas = list(db.execute(select(ComparisonArea).order_by(ComparisonArea.id)).scalars())
    return [area_payload(db, area) for area in areas]


@router.get("/areas/{area_id}/observation")
def area_observation(
    area_id: int,
    plan_id: int = Query(description="比较方案 id"),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    area = db.get(ComparisonArea, area_id)
    if area is None:
        raise HTTPException(404, "比较区域不存在")
    plan = _plan(db, plan_id)
    return area_observation_payload(db, area, plan)


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), _=Depends(get_current_user)):
    projects = list(db.execute(select(EngineeringProject).order_by(EngineeringProject.id)).scalars())
    return [project_payload(db, p) for p in projects]


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return project_payload(db, _project(db, project_id))


@router.post("/projects/{project_id}/revisions")
def revise(
    project_id: int,
    data: EngineeringDatesIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    project = _project(db, project_id)
    try:
        start = date.fromisoformat(data.actual_start) if data.actual_start else None
        end = date.fromisoformat(data.actual_end) if data.actual_end else None
    except ValueError as exc:
        raise HTTPException(422, f"日期格式无效：{exc}") from exc
    try:
        revisions = revise_project(
            db,
            project,
            actual_start=start,
            actual_end=end,
            impact_boundary=data.impact_boundary,
            note=data.note,
            actor=user.username,
        )
    except DomainError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    return {"project": project_payload(db, project), "revisions": len(revisions)}


@router.post("/projects/{project_id}/phases")
def add_phase(
    project_id: int,
    data: EngineeringPhaseIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    project = _project(db, project_id)
    try:
        start = date.fromisoformat(data.actual_start)
        end = date.fromisoformat(data.actual_end) if data.actual_end else None
    except ValueError as exc:
        raise HTTPException(422, f"日期格式无效：{exc}") from exc
    if end is not None and end < start:
        raise HTTPException(422, "实际完工日期不能早于开工日期")
    target = add_construction_phase(
        db,
        project,
        phase_code=data.phase_code,
        name=data.name,
        actual_start=start,
        actual_end=end,
        boundary=data.boundary,
        source_type=data.source_type,
        source_ref=data.source_ref,
        note=data.note,
        actor=user.username,
        concurrent_code=data.concurrent_project_code,
        concurrent_name=data.concurrent_project_name,
    )
    db.commit()
    return project_payload(db, target)


@router.post("/availability-notes")
def add_availability_note(
    data: AvailabilityNoteIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    area = db.get(ComparisonArea, data.area_id)
    if area is None:
        raise HTTPException(404, "比较区域不存在")
    try:
        survey_date = date.fromisoformat(data.survey_date)
    except ValueError as exc:
        raise HTTPException(422, f"日期格式无效：{exc}") from exc
    note = SurveyAvailabilityNote(
        area_id=area.id,
        station_id=data.station_id,
        survey_date=survey_date,
        kind=data.kind,
        effort_scale=data.effort_scale,
        reason=data.reason,
        created_by=user.username,
    )
    db.add(note)
    db.flush()
    _audit(
        db,
        "survey_availability_note",
        note.id,
        "comparison.availability_note_added",
        user.username,
        {"area_id": area.id, "survey_date": data.survey_date, "effort_scale": data.effort_scale},
    )
    db.commit()
    return {
        "id": note.id,
        "area_id": note.area_id,
        "survey_date": note.survey_date.isoformat(),
        "effort_scale": note.effort_scale,
        "reason": note.reason,
    }


# --------------------------------------------------------------------- plans
@router.get("/plans")
def list_plans(db: Session = Depends(get_db), _=Depends(get_current_user)):
    plans = list(db.execute(select(ComparisonPlan).order_by(ComparisonPlan.id)).scalars())
    return [plan_payload(db, plan) for plan in plans]


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    plan = _plan(db, plan_id)
    data = plan_payload(db, plan)
    data["missing_reason"] = plan_missing_reason(plan)
    analyses = list(
        db.execute(
            select(ComparisonAnalysis)
            .where(ComparisonAnalysis.plan_id == plan.id)
            .order_by(ComparisonAnalysis.id.desc())
        ).scalars()
    )
    data["analyses"] = [
        {
            "id": a.id,
            "analysis_code": a.analysis_code,
            "status": a.status,
            "method": a.method,
            "created_by": a.created_by,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "previous_analysis_id": a.previous_analysis_id,
            "note": a.note,
        }
        for a in analyses
    ]
    return data


@router.post("/plans/{plan_id}/analyse")
def analyse(
    plan_id: int,
    data: ComparisonAnalyseIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    plan = _plan(db, plan_id)
    missing = plan_missing_reason(plan)
    if missing:
        raise HTTPException(409, missing)
    if plan.status != "locked":
        raise HTTPException(409, "比较方案尚未锁定：需先固定处理区、候选参照区、观察窗口与纳入依据")
    from ..restoration import analyse_plan

    method = data.method
    analysis = analyse_plan(db, plan, method=method, actor=user.username, note=data.note)
    db.commit()
    return analysis_payload(db, analysis)


@router.post("/plans/{plan_id}/recompute")
def recompute(
    plan_id: int,
    data: ComparisonRecomputeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    plan = _plan(db, plan_id)
    missing = plan_missing_reason(plan)
    if missing:
        raise HTTPException(409, missing)
    previous = latest_analysis(db, plan.id)
    try:
        analysis, diff = recompute_plan(
            db,
            plan,
            method=data.method,
            actor=user.username,
            note=data.note,
            input_revision=(
                {"summary": data.note or "按当前工程输入重新计算。"} if previous else None
            ),
        )
    except DomainError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    payload = analysis_payload(db, analysis)
    payload["diff"] = diff
    return payload


@router.get("/plans/{plan_id}/analyses")
def list_analyses(plan_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    _plan(db, plan_id)
    rows = list(
        db.execute(
            select(ComparisonAnalysis)
            .where(ComparisonAnalysis.plan_id == plan_id)
            .order_by(ComparisonAnalysis.id.desc())
        ).scalars()
    )
    return [
        {
            "id": a.id,
            "analysis_code": a.analysis_code,
            "status": a.status,
            "method": a.method,
            "created_by": a.created_by,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "previous_analysis_id": a.previous_analysis_id,
            "input_batch": a.input_batch,
            "note": a.note,
        }
        for a in rows
    ]


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return analysis_payload(db, _analysis(db, analysis_id))
