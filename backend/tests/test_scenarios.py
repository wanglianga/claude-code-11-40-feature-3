"""Acceptance tests for the seven required evidence scenarios."""
from __future__ import annotations




def _units_by_station(client, auth, run_id, station_code):
    units = client.get(f"/api/runs/{run_id}/units", headers=auth).json()
    return [u for u in units if u["station_code"] == station_code]


def _gate(unit_detail, gate):
    return next(g for g in unit_detail["gates"] if g["gate"] == gate)


def test_camera_frame_gap_marks_device_pending(client, auth, run_id):
    """Camera heartbeat present but consecutive frames missing and no downtime log."""
    for unit in _units_by_station(client, auth, run_id, "QSL-02"):
        detail = client.get(f"/api/units/{unit['id']}", headers=auth).json()
        device = _gate(detail, "device")
        assert device["status"] == "pending"
        assert device["measured"]["unexplained_gap_hours"] > 0
        assert device["measured"]["unexplained_gaps"]
        assert detail["certificate"]["status"] == "pending"
        assert detail["certificate"]["absence_eligible"] is False
        assert "待核" in detail["certificate"]["summary"]


def test_route_edge_with_coarse_precision_is_spatial_pending(client, auth, run_id):
    """Track only skims the buffer and GPS precision exceeds buffer width."""
    for unit in _units_by_station(client, auth, run_id, "YXG-01"):
        detail = client.get(f"/api/units/{unit['id']}", headers=auth).json()
        space = _gate(detail, "space")
        assert space["status"] == "pending"
        assert space["measured"]["edge_only"] is True
        assert space["measured"]["precision_ok"] is False
        assert space["measured"]["track_precision_m"] > space["measured"]["buffer_width_m"]
        assert detail["certificate"]["absence_eligible"] is False


def test_late_determination_revision_puts_certificate_under_review(client, auth, run_id, db):
    """A late revision that flips presence must suspend references, keep old cert."""
    units = _units_by_station(client, auth, run_id, "YXG-02")
    target = next(u for u in units if u["unit_date"] == "2026-04-11")
    detail = client.get(f"/api/units/{target['id']}", headers=auth).json()
    assert detail["certificate"]["review_status"] == "under_review"
    assert detail["certificate"]["trend_eligible"] is False
    assert detail["certificate"]["absence_eligible"] is False
    assert "初判遗漏" in detail["certificate"]["review_reason"]

    material_no = [e["certificate_no"] for e in detail["certificate_history"]]
    assert len(material_no) >= 2, "review certificate must be a new version"
    original = next(c for c in detail["certificate_history"] if c["version"] == 1)
    assert original["superseded_by_id"] is not None

    response = client.post(
        f"/api/units/{target['id']}/trend-references",
        headers=auth,
        json={"purpose": "trend", "note": "复审期间不得新增引用"},
    )
    assert response.status_code == 409
    assert "复审" in response.json()["detail"]

    prior = [r for r in detail["trend_references"] if r["status"] == "suspended"]
    assert prior, "earlier trend reference should be suspended, not deleted"
    assert prior[0]["snapshot"]["certificate_status"] == "valid"

    dets = [d for m in detail["evidence"]["materials"] for d in m["determinations"]]
    assert any(d["status"] == "superseded" for d in dets)
    assert any(d["status"] == "active" and d["verdict"] == "present" for d in dets)


def test_clock_drift_without_calibration_fails_season_window(client, auth, run_id):
    for unit in _units_by_station(client, auth, run_id, "BHP-01"):
        detail = client.get(f"/api/units/{unit['id']}", headers=auth).json()
        time_gate = _gate(detail, "time")
        assert time_gate["status"] == "fail"
        assert time_gate["measured"]["untrusted_count"] > 0
        assert "可验证校时关系" in time_gate["explanation"]
        assert detail["certificate"]["status"] == "invalid"
        assert detail["certificate"]["absence_eligible"] is False


def test_verifiable_calibration_allows_clean_unit(client, auth, run_id):
    """QSL-01 has a verified calibration relationship, so drift does not block it."""
    for unit in _units_by_station(client, auth, run_id, "QSL-01"):
        detail = client.get(f"/api/units/{unit['id']}", headers=auth).json()
        time_gate = _gate(detail, "time")
        assert time_gate["status"] == "pass"
        assert time_gate["measured"]["calibrated_count"] > 0
        assert detail["certificate"]["status"] == "valid"
        assert detail["certificate"]["absence_eligible"] is True


def test_station_migration_uses_version_at_event_time(client, auth, run_id, db):
    units = _units_by_station(client, auth, run_id, "BHP-02")
    version_by_date = {u["unit_date"]: u["station_version_id"] for u in units}
    assert version_by_date["2026-04-11"] != version_by_date["2026-04-12"]

    before = next(u for u in units if u["unit_date"] == "2026-04-11")
    after = next(u for u in units if u["unit_date"] == "2026-04-12")
    before_detail = client.get(f"/api/units/{before['id']}", headers=auth).json()
    after_detail = client.get(f"/api/units/{after['id']}", headers=auth).json()
    assert before_detail["station_version"]["version_number"] == 1
    assert after_detail["station_version"]["version_number"] == 2
    assert after_detail["station_version"]["effective_from"] == "2026-04-12"

    # The migrated route segment only exists from 2026-04-12.
    after_segments = {s["segment_code"] for s in _gate(after_detail, "space")["measured"]["segment_coverages"]}
    assert "SEG-BHP-02-B" in after_segments
    before_segments = {s["segment_code"] for s in _gate(before_detail, "space")["measured"]["segment_coverages"]}
    assert "SEG-BHP-02-B" not in before_segments
    assert before_detail["certificate"]["status"] == "valid"
    assert after_detail["certificate"]["status"] in {"valid", "restricted"}


def test_conflicting_determinations_are_preserved(client, auth, run_id):
    units = _units_by_station(client, auth, run_id, "QSL-03")
    target = next(u for u in units if u["unit_date"] == "2026-04-11")
    detail = client.get(f"/api/units/{target['id']}", headers=auth).json()
    reading = _gate(detail, "reading")
    assert reading["status"] == "pending"
    assert reading["measured"]["open_conflicts"] == 1
    assert detail["certificate"]["absence_eligible"] is False

    conflicts = client.get("/api/conflicts?status=open", headers=auth).json()
    assert len(conflicts) >= 1
    conflict = next(c for c in conflicts if c["unit_id"] == target["id"])
    verdicts = {s["verdict"] for s in conflict["sources"]}
    assert verdicts == {"present", "absent"}
    assert all(s["confidence"] < reading["threshold"]["min_confidence"] for s in conflict["sources"])
    assert detail["certificate"]["conclusions"]["absence_analysis"]["open_conflicts"] == 1
    assert "判读冲突未决" in detail["certificate"]["conclusions"]["absence_analysis"]["species_note"]

    resolved = client.post(
        f"/api/conflicts/{conflict['id']}/resolve",
        headers=auth,
        json={"resolution": "两位判读者复核后一致认为素材过曝，保留冲突记录并另行判读。"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"


def test_model_threshold_sensitivity_changes_usable_sample(client, auth, run_id):
    overview = client.get(f"/api/runs/{run_id}/overview", headers=auth).json()
    model = overview["model"]
    assert model["threshold"] == 0.80
    rows = client.get(
        f"/api/probability/models/{model['id']}/threshold-sensitivity", headers=auth
    ).json()["rows"]
    counts = {row["threshold"]: row["units_passing"] for row in rows}
    assert counts[0.85] > counts[0.90], "raising the threshold must reduce usable samples"
    assert counts[0.65] >= counts[0.80] >= counts[0.85]
    current = next(row for row in rows if row["threshold"] == 0.80)
    assert current["delta_from_current"] == 0

    restricted = next(
        r for r in overview["comparison_rows"]
        if r["station_code"] == "YXG-03" and r["divergence"] == "probability_supports_rule_blocks"
    )
    assert restricted["absence_eligible"] is False
    assert restricted["threshold_pass"] is True
    assert restricted["interval"][0] < restricted["probability"] < restricted["interval"][1]

    estimate = client.post(
        f"/api/probability/models/{model['id']}/evaluate?unit_id={restricted['unit_id']}&calibration_delta=0.04",
        headers=auth,
    ).json()
    assert estimate["purpose"] == "trend_only"
    assert "不是已确认的实地覆盖" in estimate["disclaimer"]
    baseline = next(v for v in estimate["variants"] if v["key"] == "baseline")
    prior = next(v for v in estimate["variants"] if v["key"] == "prior_calibration")
    assert prior["probability"] < baseline["probability"]


def test_absence_reference_requires_valid_certificate(client, auth, run_id):
    units = _units_by_station(client, auth, run_id, "QSL-01")
    unit = units[0]
    ok = client.post(
        f"/api/units/{unit['id']}/trend-references",
        headers=auth,
        json={"purpose": "absence", "note": "规则证书完整，可用于正式未发现结论"},
    )
    assert ok.status_code == 200
    assert ok.json()["purpose"] == "absence"

    restricted = _units_by_station(client, auth, run_id, "YXG-03")[0]
    blocked = client.post(
        f"/api/units/{restricted['id']}/trend-references",
        headers=auth,
        json={"purpose": "absence", "note": "受限证书不应通过"},
    )
    assert blocked.status_code == 409
    trend_ok = client.post(
        f"/api/units/{restricted['id']}/trend-references",
        headers=auth,
        json={"purpose": "trend", "note": "仅作趋势参考"},
    )
    assert trend_ok.status_code == 200


def test_traceability_chain_is_complete(client, auth, run_id):
    units = _units_by_station(client, auth, run_id, "QSL-01")
    trace = client.get(f"/api/units/{units[0]['id']}/trace", headers=auth).json()
    steps = {step["step"]: step for step in trace["chain"]}
    assert {"raw_events", "coverage_conditions", "versions", "certificate", "trend_references"} <= set(steps)
    records = steps["raw_events"]["items"]
    assert records and all(len(r["hash"]) == 64 for r in records)
    assert all(r["event_time"] and r["received_time"] and r["source_device"] for r in records)
    assert steps["coverage_conditions"]["items"]
    kinds = {item["kind"] for item in steps["versions"]["items"]}
    assert kinds == {"rulebook", "station_version", "probability_model"}
    assert steps["certificate"]["items"]
    assert trace["unit"]["station_version_id"]


def test_service_emits_no_dispatch_actions(client, auth, run_id):
    """No endpoint or payload may create patrol/booking/enforcement actions."""
    forbidden = ("dispatch", "assign", "schedule_patrol", "enforce", "capture", "resource_order")
    overview = client.get(f"/api/runs/{run_id}/overview", headers=auth)
    body = overview.text.lower()
    for word in forbidden:
        assert word not in body
    schema = client.get("/openapi.json").json()
    paths = " ".join(schema["paths"].keys()).lower()
    for word in forbidden:
        assert word not in paths
