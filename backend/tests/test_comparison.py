"""Acceptance tests for the wetland restoration survey-comparison workspace.

Sequence mirrors the required acceptance run:

1. Compute a real result from one treatment area and several candidate references.
2. Put a reference under construction impact → affected conclusions pause and the
   earlier report keeps its own evidence.
3. Remove part of a season's surveys → the missing dates are recorded, never
   read as zero observations.
4. Correct the engineering start date after the fact → results are recomputed and
   the change is attributed to the input revision, with each unit traceable.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.models import ComparisonAnalysis, ComparisonPlan, EngineeringProject
from app.restoration import (
    METHOD_DESCRIPTIVE,
    METHOD_SINGLE,
    METHOD_WEIGHTED,
    analyse_plan,
)
from app.seed_wetland import square_m


def _plan_payload(client, auth):
    plans = client.get("/api/comparison/plans", headers=auth).json()
    plan = next(p for p in plans if p["plan_code"] == "PLAN-WR-2026-01")
    return plan


def _project_id(client, auth, plan):
    return plan["treatment_project_id"]


def _analyse(client, auth, plan_id, method=METHOD_WEIGHTED):
    response = client.post(
        f"/api/comparison/plans/{plan_id}/analyse",
        headers=auth,
        json={"method": method, "note": "acceptance baseline"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _recompute(client, auth, plan_id, note):
    response = client.post(
        f"/api/comparison/plans/{plan_id}/recompute",
        headers=auth,
        json={"note": note},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def plan(client, seeded):
    auth = {
        "Authorization": "Bearer "
        + client.post(
            "/api/auth/login", json={"username": "yq.li", "password": "Survey#2026"}
        ).json()["token"]
    }
    return _plan_payload(client, auth), auth


def test_base_comparison_uses_pre_only_weights(client, plan, db):
    payload, auth = plan
    data = _analyse(client, auth, payload["id"])
    assert data["status"] == "computed"
    assert data["method"] == METHOD_WEIGHTED

    # A real result: every post-restoration date has a treatment rate and a
    # weighted reference rate, with the combination fitted on pre-season only.
    assert {row["date"] for row in data["results"]} == {
        "2026-04-10",
        "2026-04-11",
        "2026-04-12",
    }
    assert all(row["difference"] is not None for row in data["results"])
    assert data["pre"]["common_pre_dates"] == ["2025-04-10", "2025-04-11", "2025-04-12"]

    included = [w for w in data["weights"] if w["status"] == "included"]
    assert len(included) >= 2, "multiple references must form the weighted combination"
    assert abs(sum(w["weight"] for w in included) - 1.0) < 0.01

    # Every candidate is shown with either a weight or a reason for exclusion.
    excluded = [w for w in data["weights"] if w["status"] == "excluded"]
    assert excluded, "the reference with a missing pre-season must be excluded"
    for item in data["weights"]:
        assert item["status"] in {"included", "excluded"}
        if item["status"] == "excluded":
            assert item["reason"]
    codes = {e["code"] for e in data["exclusions"]}
    assert "WR-R5" in codes

    # Pre-restoration fit error, held-out pre date and leave-one-out are reported.
    assert data["sensitivity"]["pre_fit_errors"]
    assert data["sensitivity"]["held_out_pre_dates"]
    assert data["sensitivity"]["leave_one_out"]
    assert data["diagnostics"]["weight_stability"] in {"stable", "unstable"}

    # Conclusions always carry a non-applicable scope.
    for item in data["conclusions"]:
        assert item["not_applicable"]

    # Effort is calibrated per unit; raw photo counts are never the rate.
    area = next(a for a in data["areas"] if a["code"] == "WR-T1")
    unit = area["units"][0]
    assert unit["effective_effort"]["effective_effort_days"] > 0
    assert unit["observation_rate"] == pytest.approx(
        unit["detections"] / unit["effective_effort"]["effective_effort_days"], rel=1e-3
    )
    assert "照片总数" in unit["effective_effort"]["note"]


def test_weights_ignore_post_restoration_outcomes(client, plan, db):
    """Dropping every post-restoration survey must not change a single weight."""
    payload, auth = plan
    baseline = _analyse(client, auth, payload["id"], METHOD_WEIGHTED)
    baseline_weights = {
        w["code"]: w["weight"] for w in baseline["weights"] if w["status"] == "included"
    }

    plan_row = db.get(ComparisonPlan, payload["id"])
    from app.restoration import analyse_plan as engine_analyse

    # Fit on the pre-restoration window only, using a plan variant restricted to it.
    plan_row.observation_end = date(2026, 4, 9)
    db.flush()
    pre_only = engine_analyse(db, plan_row, method=METHOD_WEIGHTED, actor="test")
    pre_weights = {
        w["code"]: w["weight"] for w in pre_only.weights if w.get("status") == "included"
    }
    db.rollback()
    assert pre_weights == pytest.approx(
        {code: baseline_weights[code] for code in pre_weights}, rel=0.02
    )


def test_reference_construction_pauses_only_affected_dates(client, plan, db):
    payload, auth = plan
    baseline = _analyse(client, auth, payload["id"])

    # The three references that carry most of the pre-fitted weight start work
    # inside the observation season. Their remaining dates retain too little
    # weight for the combination to hold.
    project_id = _project_id(client, auth, payload)
    for code, name, lat, lon in (
        ("WR-ACCEPT-R1", "西塘参照区同期施工", 31.2065, 103.772),
        ("WR-ACCEPT-R2", "南汊参照区同期施工", 31.1930, 103.760),
        ("WR-ACCEPT-R3", "北汊参照区同期施工", 31.2180, 103.764),
    ):
        response = client.post(
            f"/api/comparison/projects/{project_id}/phases",
            headers=auth,
            json={
                "phase_code": "C1",
                "name": name,
                "actual_start": "2026-04-11",
                "actual_end": None,
                "boundary": square_m(lat, lon, 160),
                "source_type": "施工月报",
                "source_ref": "验收：参照区开工",
                "note": "参照区在观察季中开工",
                "concurrent_project_code": code,
                "concurrent_project_name": name,
            },
        )
        assert response.status_code == 200, response.text

    revised = _recompute(client, auth, payload["id"], "参照区在观察季中开工")
    assert revised["analysis_code"] != baseline["analysis_code"]

    # The affected dates lose their difference; unaffected dates survive.
    by_date = {row["date"]: row for row in revised["results"]}
    assert by_date["2026-04-11"]["sufficient"] is False
    assert by_date["2026-04-11"]["difference"] is None
    assert by_date["2026-04-10"]["sufficient"] is True
    assert revised["status"] == "under_review"

    # Pause carries unit-level traceability and points at the earlier report.
    paused = revised["diff"]["paused"]
    assert paused and paused[0]["previous_analysis_code"] == baseline["analysis_code"]
    affected = {unit for entry in paused for unit in entry["affected_unit_ids"]}
    assert any("WR-R1:2026-04-11" == unit for unit in affected)

    # A unit-level removal in the input batch traces the change to the revision.
    assert "WR-R1:2026-04-11" in revised["diff"]["units_removed"]
    assert revised["diff"]["engineering_input_changed"] is True
    assert revised["diff"]["survey_input_changed"] is True

    # The old report still returns its original numbers and status.
    old = client.get(f"/api/comparison/analyses/{baseline['id']}", headers=auth).json()
    assert old["status"] == baseline["status"]
    assert [row["difference"] for row in old["results"]] == [
        row["difference"] for row in baseline["results"]
    ]

    # A description of the impact is visible on the candidate.
    r1 = next(c for c in revised["candidates"] if c["code"] == "WR-R1")
    assert r1["impacted_units"], "the impacted reference must list its affected units"


def test_missing_season_surveys_are_reported_not_zeroed(client, plan):
    payload, auth = plan
    data = _analyse(client, auth, payload["id"])
    r5 = next(a for a in data["areas"] if a["code"] == "WR-R5")
    assert r5["missing_dates"] == ["2025-04-10", "2025-04-11", "2025-04-12"]
    assert r5["missing_detail"]
    assert "不计为 0 次观测" in r5["missing_detail"][0]["reason"]
    excluded = next(w for w in data["weights"] if w["code"] == "WR-R5")
    assert excluded["status"] == "excluded"
    assert "修复前" in excluded["reason"]


def test_descriptive_fallback_without_common_pre_window(client, plan, db):
    payload, auth = plan
    plan_row = db.get(ComparisonPlan, payload["id"])
    original = dict(plan_row.criteria or {})
    plan_row.criteria = {**original, "min_common_pre_dates": 99}
    db.commit()
    try:
        engine = analyse_plan(db, plan_row, method=METHOD_WEIGHTED, actor="test")
        db.commit()
        report = client.get(
            f"/api/comparison/analyses/{engine.id}", headers=auth
        ).json()
        assert report["status"] == METHOD_DESCRIPTIVE
        assert report["weights"] == [] or all(
            w["status"] == "excluded" for w in report["weights"]
        )
        assert report["results"] == [], "no differences are fitted without a common pre window"
        assert report["descriptive"], "descriptive differences are still reported"
        assert any("不强行拟合" in reason for reason in report["reasons"])
    finally:
        plan_row.criteria = original
        db.commit()


def test_engineering_date_correction_recomputes_and_attributes(client, plan, db):
    payload, auth = plan
    baseline = _analyse(client, auth, payload["id"])
    project_id = _project_id(client, auth, payload)

    response = client.post(
        f"/api/comparison/projects/{project_id}/revisions",
        headers=auth,
        json={
            "actual_start": "2026-03-01",
            "actual_end": "2026-03-18",
            "note": "验收：工程开始日期回溯更正（实际开工早于台账）",
        },
    )
    assert response.status_code == 200, response.text
    revised = _recompute(client, auth, payload["id"], "工程开始日期回溯更正")

    # The start-date correction moves the window boundary; the same survey days are
    # re-classified by the corrected dates, never duplicated across pre and post.
    assert revised["pre"]["construction_start"] == "2026-03-01"
    assert revised["pre"]["construction_end"] == "2026-03-18"
    post_dates = {row["date"] for row in revised["results"]}
    assert post_dates == {"2026-04-10", "2026-04-11", "2026-04-12"}
    assert not post_dates & set(revised["pre"]["common_pre_dates"])
    # Survey days inside the corrected construction period are excluded explicitly,
    # neither baseline nor outcome.
    assert revised["pre"]["construction_period_dates"] == []
    assert revised["change_log"] and revised["change_log"][-1]["source"] == "input_revision"
    assert revised["diff"]["source"] in {
        "engineering_input_revision",
        "input_revision",
        "input_revision_and_plan_change",
    }
    assert revised["diff"]["engineering_input_changed"] is True
    assert revised["analysis_code"] != baseline["analysis_code"]

    # Every changed unit is individually traceable.
    for unit_key in revised["diff"]["units_added"]:
        assert ":" in unit_key

    # Correcting the date was a revision, not a plan change.
    assert revised["input_batch"]["engineering"]["actual_start"] == "2026-03-01"
    assert revised["input_batch"]["plan_signature"] == baseline["input_batch"]["plan_signature"]


def test_backfilled_downtime_log_and_expanded_impact_boundary(client, plan, db):
    """Two more input revisions: a backfilled log, then a widened impact boundary."""
    payload, auth = plan
    baseline = _analyse(client, auth, payload["id"])

    # 1) Backfill a downtime log for a treatment survey day via the main service.
    area = next(a for a in baseline["areas"] if a["code"] == "WR-T1")
    target = next(u for u in area["units"] if u["date"] == "2026-04-12")
    station_id = target["station_id"]
    from app.seed import utc_iso

    record = client.post(
        "/api/records",
        headers=auth,
        json={
            "record_type": "downtime_log",
            "external_id": "DT-WRT-01-20260412-BACKFILL",
            "station_id": station_id,
            "event_time": utc_iso("2026-04-12", 13),
            "received_time": utc_iso("2026-04-12", 20),
            "source_device": "CAM-WRT-01",
            "payload": {
                "start": utc_iso("2026-04-12", 9, 0),
                "end": utc_iso("2026-04-12", 12, 0),
                "reason": "补录：停机日志遗漏",
                "camera_id": "CAM-WRT-01",
            },
        },
    )
    assert record.status_code == 200, record.text

    # Rebuild the wetland run so the unit re-reads the current records.
    runs = client.get("/api/runs", headers=auth).json()
    wetland_run = next(r for r in runs if r["run_code"].startswith("RUN-2025-SPRING"))
    rebuild = client.post(f"/api/runs/{wetland_run['id']}/rebuild", headers=auth)
    assert rebuild.status_code == 200, rebuild.text

    after_log = _recompute(client, auth, payload["id"], "补录停机日志后重算")
    assert after_log["analysis_code"] != baseline["analysis_code"]
    assert after_log["diff"]["source"] in {
        "engineering_input_revision",
        "survey_input_revision",
        "input_revision",
        "input_revision_and_plan_change",
        "recompute_same_inputs",
    }
    # The change is attributable to a survey input revision, not to a plan change.
    assert after_log["diff"]["survey_input_changed"] is True
    assert after_log["input_batch"]["plan_signature"] == baseline["input_batch"]["plan_signature"]

    # 2) Widen the project's actual range so it now reaches the WR-R4 reference
    # station inside the observation season.
    project = db.get(EngineeringProject, payload["treatment_project_id"])
    r4_lat, r4_lon = 31.188, 103.741
    widened = square_m(r4_lat, r4_lon, 400)
    response = client.post(
        f"/api/comparison/projects/{project.id}/phases",
        headers=auth,
        json={
            "phase_code": "P4",
            "name": "边界扩大后的补充施工",
            "actual_start": "2026-04-11",
            "actual_end": "2026-04-15",
            "boundary": widened,
            "source_type": "施工月报",
            "source_ref": "月报 2026-04（补）",
            "note": "影响边界扩大：一并覆盖下湾参照站",
        },
    )
    assert response.status_code == 200, response.text

    after_boundary = _recompute(client, auth, payload["id"], "影响边界扩大后重算")
    r4 = next(c for c in after_boundary["candidates"] if c["code"] == "WR-R4")
    assert r4["impacted_units"], "the widened boundary must exclude the WR-R4 units"
    excluded_keys = {u["unit_id"] for u in r4["impacted_units"]}
    assert excluded_keys, "impact must be traceable to specific units"
    assert after_boundary["diff"]["engineering_input_changed"] is True
    assert set(after_boundary["diff"]["units_removed"]) & set(
        f"WR-R4:{u['date']}" for u in r4["impacted_units"]
    )


def test_single_reference_method_is_explicit(client, plan):
    payload, auth = plan
    data = _analyse(client, auth, payload["id"], METHOD_SINGLE)
    included = [w for w in data["weights"] if w["status"] == "included"]
    assert len(included) == 1
    assert included[0]["weight"] == 1.0
    assert data["diagnostics"]["weight_stability"] == "single_reference"
    assert any("单参照" in (w.get("reason") or "") for w in data["weights"] if w["status"] == "excluded")


def test_availability_note_calibrates_effort_without_touching_records(client, plan, db):
    """A recorded effort constraint lowers the comparison effort, not the raw archive."""
    payload, auth = plan
    before = _analyse(client, auth, payload["id"])
    treatment_before = next(a for a in before["areas"] if a["code"] == "WR-T1")
    unit_before = next(u for u in treatment_before["units"] if u["date"] == "2026-04-11")

    area_id = treatment_before["area_id"]
    response = client.post(
        "/api/comparison/availability-notes",
        headers=auth,
        json={
            "area_id": area_id,
            "survey_date": "2026-04-11",
            "kind": "weather",
            "effort_scale": 0.5,
            "reason": "当日上午强降雨，调查时长缩短一半",
        },
    )
    assert response.status_code == 200, response.text

    after = _recompute(client, auth, payload["id"], "补录调查可用性说明")
    treatment_after = next(a for a in after["areas"] if a["code"] == "WR-T1")
    unit_after = next(u for u in treatment_after["units"] if u["date"] == "2026-04-11")
    assert unit_after["effective_effort"]["effective_effort_days"] == pytest.approx(
        unit_before["effective_effort"]["effective_effort_days"] / 2, rel=0.01
    )
    assert unit_after["detections"] == unit_before["detections"], "detections are not edited"
    assert unit_after["availability_notes"] and unit_after["availability_notes"][0]["effort_scale"] == 0.5
    assert after["diff"]["survey_input_changed"] is True


def test_no_approval_or_scoring_endpoints(client, auth):
    forbidden = ("approve", "approval", "score", "ranking", "award", "评优")
    schema = client.get("/openapi.json").json()
    paths = " ".join(schema["paths"].keys()).lower()
    for word in forbidden:
        assert word not in paths
    plans = client.get("/api/comparison/plans", headers=auth).json()
    body = str(plans).lower()
    for word in forbidden:
        assert word not in body


def test_report_frozen_and_linked(client, plan, db):
    payload, auth = plan
    first = _analyse(client, auth, payload["id"])
    second = _recompute(client, auth, payload["id"], "重复计算")
    assert second["previous_analysis_id"] == first["id"]
    stored_first = db.get(ComparisonAnalysis, first["id"])
    stored_second = db.get(ComparisonAnalysis, second["id"])
    assert stored_first is not None and stored_second is not None
    assert stored_first.previous_analysis_id is None
    assert stored_second.previous_analysis_id == first["id"]
    # Frozen: reading the earlier report cannot change its stored results.
    before = stored_first.results
    client.get(f"/api/comparison/analyses/{first['id']}", headers=auth)
    db.refresh(stored_first)
    assert stored_first.results == before
