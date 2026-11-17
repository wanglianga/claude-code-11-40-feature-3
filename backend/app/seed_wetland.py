"""Deterministic seed data for the wetland restoration comparison workspace.

Scenario map (area → what it demonstrates):
  WR-T1 东塘修复区      处理区；2025 春季为修复前基线，2026 春季为修复后
  WR-R1 西塘参照区      生境与修复前变化最接近 → 加权最高
  WR-R2 南汊参照区      修复前接近、修复后稳定 → 组合中的稳定参照
  WR-R3 北汊参照区      修复前波动较大 → 权重较低，去一参照时影响明显
  WR-R4 下湾参照区      海拔带不同 → 生境相似度低，权重低
  WR-R5 林边参照区      2025 春季调查缺失 → 修复前证据不足，仅作描述性对照
  WR-R6 沙洲参照区      生境类型不同 → 低权重，解释组合边界

Engineering record WR-2025-01 covers 2025-10 → 2026-03 (outside both survey
seasons). Acceptance exercises then append a construction phase inside the 2026
observation season, a survey gap, and an engineering date correction.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select

from .models import (
    ComparisonArea,
    ComparisonAreaStation,
    ComparisonPlan,
    ComparisonPlanMember,
    EngineeringPhase,
    EngineeringProject,
    Rulebook,
    RulebookVersion,
    Station,
    StationVersion,
    SurveyUnit,
    Zone,
)
import math

from .seed import (
    _record,
    latlon,
    line_geojson,
    requirements,
    route_segments,
    square_boundary,
    utc_iso,
)

LAT_M = 110_540.0
LON_M = 111_320.0


def square_m(lat0: float, lon0: float, half: float) -> dict:
    """Accurate metre-scale square (the shared seed helper uses a display-scaled degree)."""
    lon_deg = half / (LON_M * math.cos(math.radians(lat0)))
    lat_deg = half / LAT_M
    coords = [
        [round(lon0 - lon_deg, 7), round(lat0 - lat_deg, 7)],
        [round(lon0 + lon_deg, 7), round(lat0 - lat_deg, 7)],
        [round(lon0 + lon_deg, 7), round(lat0 + lat_deg, 7)],
        [round(lon0 - lon_deg, 7), round(lat0 + lat_deg, 7)],
        [round(lon0 - lon_deg, 7), round(lat0 - lat_deg, 7)],
    ]
    return {"type": "Polygon", "coordinates": [coords]}
from .services import add_determination, build_run, create_trend_reference

PRE_DATES = ["2025-04-10", "2025-04-11", "2025-04-12"]
POST_DATES = ["2026-04-10", "2026-04-11", "2026-04-12"]

WATERBIRDS = [
    {"code": "ANSER_INDICUS", "name": "斑头雁", "min_detections": 2},
    {"code": "ANAS_CRECCA", "name": "绿翅鸭", "min_detections": 2},
    {"code": "ARD_CINEREA", "name": "苍鹭", "min_detections": 1},
]

# code, name, lat, lon, elev, habitat, detection plan pre / post (None = season missing)
WETLAND_STATIONS = [
    (
        "WRT-01",
        "东塘修复区监测站",
        "WR",
        31.205000,
        103.755000,
        (1520, 1660),
        "marsh",
        "LOW",
        {"pre": [2, 3, 2], "post": [4, 5, 4]},
        "treatment",
    ),
    (
        "WRR-01",
        "西塘参照站",
        "WR",
        31.206500,
        103.772000,
        (1530, 1670),
        "marsh",
        "LOW",
        {"pre": [2, 3, 2], "post": [2, 2, 3]},
        "reference",
    ),
    (
        "WRR-02",
        "南汊参照站",
        "WR",
        31.193000,
        103.760000,
        (1510, 1650),
        "marsh",
        "LOW",
        {"pre": [2, 2, 3], "post": [2, 3, 2]},
        "reference",
    ),
    (
        "WRR-03",
        "北汊参照站",
        "WR",
        31.218000,
        103.764000,
        (1540, 1680),
        "marsh",
        "LOW",
        {"pre": [1, 3, 2], "post": [3, 2, 3]},
        "reference",
    ),
    (
        "WRR-04",
        "下湾参照站",
        "WR",
        31.188000,
        103.741000,
        (1810, 1960),
        "marsh",
        "MID",
        {"pre": [3, 2, 2], "post": [2, 2, 2]},
        "reference",
    ),
    (
        "WRR-05",
        "林边参照站",
        "WR",
        31.210000,
        103.781000,
        (1550, 1690),
        "marsh",
        "LOW",
        {"pre": None, "post": [2, 2, 3]},
        "reference",
    ),
    (
        "WRR-06",
        "沙洲参照站",
        "WR",
        31.201000,
        103.735000,
        (1500, 1620),
        "wet_meadow",
        "LOW",
        {"pre": [1, 1, 2], "post": [1, 2, 1]},
        "reference",
    ),
]

AREA_DEFS = [
    (
        "WR-T1",
        "东塘修复区",
        "treatment",
        "WRT-01",
        (1520, 1660),
        {"code": "marsh", "label": "浅水沼泽", "elevation_band": "LOW"},
        "2024 年退耕还湿后实施水文连通与植被重建的修复区。",
    ),
    (
        "WR-R1",
        "西塘参照区",
        "reference",
        "WRR-01",
        (1530, 1670),
        {"code": "marsh", "label": "浅水沼泽", "elevation_band": "LOW"},
        "与修复区同海拔、同生境，修复前季节变化最接近。",
    ),
    (
        "WR-R2",
        "南汊参照区",
        "reference",
        "WRR-02",
        (1510, 1650),
        {"code": "marsh", "label": "浅水沼泽", "elevation_band": "LOW"},
        "由南汊自然来水维持，修复前后变化稳定。",
    ),
    (
        "WR-R3",
        "北汊参照区",
        "reference",
        "WRR-03",
        (1540, 1680),
        {"code": "marsh", "label": "浅水沼泽", "elevation_band": "LOW"},
        "生境接近但修复前观测波动较大，权重应较低。",
    ),
    (
        "WR-R4",
        "下湾参照区",
        "reference",
        "WRR-04",
        (1810, 1960),
        {"code": "marsh", "label": "浅水沼泽", "elevation_band": "MID"},
        "海拔带不同，生境相似度低，用于检验加权边界。",
    ),
    (
        "WR-R5",
        "林边参照区",
        "reference",
        "WRR-05",
        (1550, 1690),
        {"code": "marsh", "label": "浅水沼泽", "elevation_band": "LOW"},
        "2025 春季未开展完整调查，修复前证据不足。",
    ),
    (
        "WR-R6",
        "沙洲参照区",
        "reference",
        "WRR-06",
        (1500, 1620),
        {"code": "wet_meadow", "label": "湿草甸", "elevation_band": "LOW"},
        "生境类型不同，作为低权重候选并记录排除边界。",
    ),
]

ENGINEERING_CODE = "WR-2025-01"


def _wetland_rulebook(db) -> dict[str, RulebookVersion]:
    master = Rulebook(
        code="RB-WETLAND-SPRING",
        name="湿地修复春季水鸟调查规则册",
        zone_id=db.execute(select(Zone).where(Zone.code == "WR")).scalar_one().id,
        description="湿地修复比较工作区使用：目标物种为春季水鸟，沿用四类门槛。",
    )
    db.add(master)
    db.flush()
    segments = []
    for code, _name, _zone, lat, lon, _elev, _hab, _band, _plan, _role in WETLAND_STATIONS:
        segments.extend(route_segments(code, lat, lon, 100))
    versions: dict[str, RulebookVersion] = {}
    for season, start, end, dates in (
        ("2025-SPRING", date(2025, 4, 1), date(2025, 4, 30), PRE_DATES),
        ("2026-SPRING", date(2026, 4, 1), date(2026, 4, 30), POST_DATES),
    ):
        req = requirements()
        req["schedule"]["survey_dates"] = dates
        version = RulebookVersion(
            rulebook_id=master.id,
            version_number=1 if season == "2025-SPRING" else 2,
            label=f"{season} 湿地水鸟调查正式版",
            season_code=season,
            season_start=start,
            season_end=end,
            status="active",
            target_species=WATERBIRDS,
            requirements=req,
            route_segments=segments,
            elevation_bands=[
                {"code": "LOW", "name": "低海拔湿地", "min_m": 1450, "max_m": 1750},
                {"code": "MID", "name": "中海拔湿草甸", "min_m": 1750, "max_m": 2050},
            ],
            notes="只用于湿地修复调查数据比较，不提供工程审批或项目评优结论。",
        )
        db.add(version)
        db.flush()
        versions[season] = version
    return versions


def _emit_wetland_day(db, station: Station, day: str, present: int) -> None:
    device = f"CAM-{station.code}"
    for hour in range(0, 24, 2):
        _record(
            db,
            station=station,
            record_type="device_heartbeat",
            external_id=f"HB-{station.code}-{day.replace('-', '')}-{hour:02d}",
            event_time=utc_iso(day, hour),
            payload={
                "battery_pct": 82 - (hour % 5),
                "temperature_c": 9.5,
                "firmware": "3.4.0",
                "heartbeat_seq": hour // 2 + 1,
            },
            device=device,
        )
    frame_hours = [(6, 1), (8, 2), (10, 3), (12, 4), (14, 5), (16, 6)]
    materials = []
    for idx, (hour, seq) in enumerate(frame_hours):
        lat, lon = latlon(station.latitude, station.longitude, -55 + idx * 22, 6)
        record = _record(
            db,
            station=station,
            record_type="camera_event",
            external_id=f"IMG-{station.code}-{day.replace('-', '')}-{seq:04d}",
            event_time=utc_iso(day, hour),
            received_time=utc_iso(day, hour, 5),
            payload={
                "frame_seq": seq,
                "image_ref": f"s3://reserve-wetland/{station.code}/{day}/IMG_{seq:04d}.jpg",
                "camera_id": device,
                "exposure_mode": "auto",
                "species_group": "waterbird",
            },
            device=device,
            lat=lat,
            lon=lon,
            precision=8.0,
        )
        materials.append(record)
    for idx, material in enumerate(materials):
        species = WATERBIRDS[idx % len(WATERBIRDS)]["code"]
        present_here = idx < present
        add_determination(
            db,
            material_record_id=material.id,
            source="analyst",
            species_code=species,
            verdict="present" if present_here else "absent",
            confidence=0.86 if present_here else 0.83,
            analyst="湿地水鸟判读组",
            notes="水鸟逐帧判读" if present_here else "逐帧判读，未记录目标水鸟",
            actor="seed",
        )
    _record(
        db,
        station=station,
        record_type="downtime_log",
        external_id=f"DT-{station.code}-{day.replace('-', '')}",
        event_time=utc_iso(day, 10, 15),
        payload={
            "start": utc_iso(day, 10, 15),
            "end": utc_iso(day, 12, 15),
            "reason": "计划维护：更换电池与存储卡",
            "operator": "湿地巡护组",
            "camera_id": device,
        },
        device=device,
    )
    _record(
        db,
        station=station,
        record_type="patrol_track",
        external_id=f"TRK-{station.code}-{day.replace('-', '')}",
        event_time=utc_iso(day, 7),
        received_time=utc_iso(day, 19),
        payload={
            "geometry": line_geojson(station.latitude, station.longitude, -80, 0, 80, 0),
            "observer": "湿地巡护组",
            "route_code": f"SEG-{station.code}-A",
            "distance_km": 4.8,
            "elevation_gain_m": 60,
        },
        device=f"GPS-{station.code}",
        precision=9.0,
    )


def seed_wetland(db) -> dict:
    zone = Zone(code="WR", name="湿地修复样区", description="退耕还湿与水文连通修复区域及其候选参照区。")
    db.add(zone)
    db.flush()
    station_map: dict[str, Station] = {}
    for code, name, _zone, lat, lon, elev, _hab, _band, _plan, _role in WETLAND_STATIONS:
        station = Station(
            code=code,
            name=name,
            zone_id=zone.id,
            latitude=lat,
            longitude=lon,
            elevation_min_m=elev[0],
            elevation_max_m=elev[1],
            status="active",
        )
        db.add(station)
        db.flush()
        db.add(
            StationVersion(
                station_id=station.id,
                version_number=1,
                boundary=square_boundary(lat, lon, 100),
                buffer_width_m=25,
                centroid_lat=lat,
                centroid_lon=lon,
                elevation_min_m=elev[0],
                elevation_max_m=elev[1],
                effective_from=date(2024, 1, 1),
                effective_to=None,
                change_reason="湿地样区初始边界",
            )
        )
        db.flush()
        station_map[code] = station
        _record(
            db,
            station=station,
            record_type="station_metadata",
            external_id=f"META-{code}-V1",
            event_time=datetime(2024, 3, 20, 16, 0, tzinfo=timezone.utc).isoformat(),
            payload={
                "station_code": code,
                "zone_code": "WR",
                "elevation_band": _band,
                "buffer_width_m": 25,
                "habitat": _hab,
            },
            device="reserve-gis",
            received_time=datetime(2024, 3, 20, 16, 5, tzinfo=timezone.utc).isoformat(),
        )

    for code, _name, _zone, _lat, _lon, _elev, _hab, _band, plan, _role in WETLAND_STATIONS:
        station = station_map[code]
        if plan["pre"]:
            for day, present in zip(PRE_DATES, plan["pre"]):
                _emit_wetland_day(db, station, day, present)
        for day, present in zip(POST_DATES, plan["post"]):
            _emit_wetland_day(db, station, day, present)
    db.flush()

    rulebooks = _wetland_rulebook(db)
    station_ids = [s.id for s in station_map.values()]
    # Only stations actually surveyed that season get assembled units; a station
    # with no records for a season has no unit at all, which the workspace reports
    # as a missing survey date instead of a zero observation.
    pre_station_ids = [
        station_map[code].id
        for code, _n, _z, _lat, _lon, _e, _h, _b, plan, _r in WETLAND_STATIONS
        if plan["pre"]
    ]
    pre_run = build_run(
        db,
        rulebook_version=rulebooks["2025-SPRING"],
        model=None,
        station_ids=pre_station_ids,
        label="2025 春季湿地水鸟基线调查",
        actor="yq.li",
    )
    post_run = build_run(
        db,
        rulebook_version=rulebooks["2026-SPRING"],
        model=None,
        station_ids=station_ids,
        label="2026 春季湿地水鸟修复后调查",
        actor="yq.li",
    )

    # Areas: boundary covers the linked station's buffer, kept as plain squares so
    # the engineering impact test can reason about actual spatial overlap.
    area_map: dict[str, ComparisonArea] = {}
    for code, name, role, station_code, elev, habitat, description in AREA_DEFS:
        station = station_map[station_code]
        area = ComparisonArea(
            code=code,
            name=name,
            zone_id=zone.id,
            role=role,
            boundary=square_m(station.latitude, station.longitude, 140),
            centroid_lat=station.latitude,
            centroid_lon=station.longitude,
            elevation_min_m=elev[0],
            elevation_max_m=elev[1],
            habitat=habitat,
            description=description,
        )
        db.add(area)
        db.flush()
        db.add(ComparisonAreaStation(area_id=area.id, station_id=station.id))
        area_map[code] = area
    db.flush()

    treatment_station = station_map["WRT-01"]
    project = EngineeringProject(
        code=ENGINEERING_CODE,
        name="东塘湿地修复工程（水文连通与植被重建）",
        project_type="wetland_restoration",
        status="completed",
        spatial_boundary=square_m(treatment_station.latitude, treatment_station.longitude, 180),
        impact_boundary=square_m(treatment_station.latitude, treatment_station.longitude, 220),
        actual_start=date(2025, 10, 1),
        actual_end=date(2026, 3, 20),
        source_type="工程台账",
        source_ref="施工许可 2025-07 / 竣工备案 2026-03",
        description="工程资料维护空间范围、阶段、实际施工日期与来源；不含审批意见。",
        created_by="yq.li",
    )
    db.add(project)
    db.flush()
    db.add_all(
        [
            EngineeringPhase(
                project_id=project.id,
                phase_code="P1",
                name="水文连通",
                stage_type="construction",
                planned_start=date(2025, 9, 20),
                planned_end=date(2025, 11, 30),
                actual_start=date(2025, 10, 1),
                actual_end=date(2025, 11, 28),
                boundary=square_m(treatment_station.latitude, treatment_station.longitude, 180),
                source_type="施工月报",
                source_ref="月报 2025-10 / 2025-11",
                revision=1,
                note="实际开工比计划晚 11 天，以实际日期进入比较。",
            ),
            EngineeringPhase(
                project_id=project.id,
                phase_code="P2",
                name="植被重建",
                stage_type="construction",
                planned_start=date(2025, 12, 1),
                planned_end=date(2026, 3, 10),
                actual_start=date(2025, 12, 3),
                actual_end=date(2026, 3, 18),
                boundary=square_m(treatment_station.latitude, treatment_station.longitude, 200),
                source_type="施工月报",
                source_ref="月报 2025-12 至 2026-03",
                revision=1,
                note="完工日期比计划延后 8 天。",
            ),
        ]
    )
    db.flush()

    plan = ComparisonPlan(
        plan_code="PLAN-WR-2026-01",
        name="东塘修复区 2026 春季多参照加权比较",
        version=1,
        status="locked",
        season_scope=["2025-SPRING", "2026-SPRING"],
        observation_start=date(2025, 4, 1),
        observation_end=date(2026, 4, 30),
        species_codes=[s["code"] for s in WATERBIRDS],
        criteria={
            "min_common_pre_dates": 2,
            "min_pre_dates_per_area": 2,
            "min_post_dates_per_area": 1,
            "min_weight_share": 0.05,
            "max_weight_spread_pct": 60.0,
            "leave_one_out_tolerance_pct": 15.0,
        },
        treatment_project_id=project.id,
        created_by="yq.li",
        locked_by="yq.li",
        locked_at=datetime.now(timezone.utc),
        note="处理区、候选参照区、观察窗口与纳入依据已确定；权重只用修复前合格调查拟合。",
    )
    db.add(plan)
    db.flush()
    for code, _name, _role, _station, _elev, _hab, _desc in AREA_DEFS:
        role = "treatment" if code == "WR-T1" else "reference"
        db.add(
            ComparisonPlanMember(
                plan_id=plan.id,
                area_id=area_map[code].id,
                role=role,
                note="生境与修复前变化相近" if role == "reference" else "修复工程实施区",
            )
        )
    db.flush()

    # One trend input for the treatment area demonstrates that the comparison
    # workspace reads applicability from the main service instead of writing it.
    unit = db.execute(
        select(SurveyUnit)
        .where(SurveyUnit.run_id == post_run.id)
        .where(SurveyUnit.station_id == station_map["WRT-01"].id)
        .order_by(SurveyUnit.unit_date)
    ).scalars().first()
    if unit is not None:
        create_trend_reference(
            db,
            unit_id=unit.id,
            purpose="trend",
            actor="yq.li",
            note="湿地修复区水鸟趋势参考，仅作趋势输入，不构成工程影响结论。",
        )

    return {
        "zone": zone,
        "stations": station_map,
        "areas": area_map,
        "project": project,
        "plan": plan,
        "pre_run": pre_run,
        "post_run": post_run,
    }
