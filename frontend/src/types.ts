export type Status = 'valid' | 'restricted' | 'invalid' | 'pending' | 'under_review'
export type GateKey = 'device' | 'time' | 'space' | 'reading'

export interface User {
  id: number
  username: string
  display_name: string
  role: 'researcher' | 'auditor' | 'viewer'
}

export interface Gate {
  id: number
  gate: GateKey
  requirement_code: string
  status: 'pass' | 'restricted' | 'fail' | 'pending'
  measured: Record<string, any>
  threshold: Record<string, any>
  explanation: string
  rule_ref: string
  evidence: number[]
}

export interface Certificate {
  id: number
  unit_id: number
  run_id: number
  certificate_no: string
  version: number
  status: Status
  status_label: string
  absence_eligible: boolean
  trend_eligible: boolean
  review_status: 'normal' | 'under_review'
  review_status_label: string
  review_reason: string
  summary: string
  gate_snapshot: Array<Record<string, any>>
  conclusions: {
    absence_analysis?: {
      supported: boolean
      species_note: string
      confirmed_present: number
      confirmed_absent_readings: number
      pending_materials: number
      open_conflicts: number
      never_implies_absence: string[]
    }
    trend?: Record<string, any>
  }
  issued_at: string
  superseded_by_id: number | null
}

export interface EstimateVariant {
  key: string
  label: string
  probability: number
  low: number
  high: number
  passes: boolean
}

export interface ProbabilityEstimate {
  id: number
  unit_id: number
  model_id: number
  model_code: string
  model_name: string
  model_version: string
  model_status: string
  threshold: number
  probability: number
  interval_low: number
  interval_high: number
  threshold_pass: boolean
  conclusion: 'supports_partial' | 'below_threshold'
  variants: EstimateVariant[]
  assumptions: string[]
}

export interface Unit {
  id: number
  run_id: number
  station_id: number
  station_code: string
  station_name: string
  zone_id: number
  unit_date: string
  station_version_id: number
  rulebook_version_id: number
  model_version_id: number | null
  coverage_summary: Record<string, any>
  feature_vector: Record<string, any>
  gate_status: Record<GateKey, string>
  data_issues: Array<{ gate: string; gate_label: string; status: string; explanation: string }>
  assembled_at: string
  certificate: Certificate | null
  probability: ProbabilityEstimate | null
}

export interface TrendReference {
  id: number
  reference_code: string
  purpose: 'absence' | 'trend'
  purpose_label: string
  unit_id: number
  certificate_id: number | null
  status: 'active' | 'suspended' | 'revoked'
  snapshot: Record<string, any>
  created_by: string
  created_at: string
  suspended_reason: string
  suspended_at: string | null
  note: string
  station_code?: string
  station_name?: string
  unit_date?: string
}

export interface Determination {
  id: number
  material_record_id: number
  source: string
  species_code: string
  verdict: 'present' | 'absent' | 'uncertain'
  confidence: number
  analyst: string
  revision: number
  status: 'active' | 'superseded'
  notes: string
}

export interface EvidenceMaterial {
  id: number
  record_type: string
  record_type_label: string
  external_id: string
  event_time: string
  received_time: string
  source_device: string
  latitude: number | null
  longitude: number | null
  coord_precision_m: number | null
  payload: Record<string, any>
  payload_hash: string
  revision: number
  is_pending: boolean
  determinations: Determination[]
  conflicts: Array<{ id: number; sources: Array<Record<string, any>>; status: string }>
}

export interface UnitDetail extends Unit {
  station: StationDetail
  station_version: {
    id: number
    version_number: number
    buffer_width_m: number
    effective_from: string
    effective_to: string | null
    change_reason: string
    boundary: { type: string; coordinates: number[][][] }
  }
  rulebook: RulebookVersion
  gates: Gate[]
  certificate_history: Certificate[]
  conflicts: Array<{
    id: number
    species_code: string
    sources: Array<Record<string, any>>
    detail: string
    status: string
    resolution: string
    material_record_id: number
    detected_at: string
  }>
  trend_references: TrendReference[]
  evidence: {
    materials: EvidenceMaterial[]
    record_count: number
    determination_count: number
  }
  audit: AuditEntry[]
}

export interface Station {
  id: number
  code: string
  name: string
  zone_id: number
  zone: { code: string; name: string } | null
  latitude: number
  longitude: number
  elevation_min_m: number
  elevation_max_m: number
  status: string
  version_count: number
}

export interface StationDetail extends Station {
  versions: Array<{
    id: number
    version_number: number
    boundary: { type: string; coordinates: number[][][] }
    buffer_width_m: number
    centroid: { lat: number; lon: number }
    effective_from: string
    effective_to: string | null
    change_reason: string
  }>
}

export interface RulebookVersion {
  id: number
  rulebook_code: string
  rulebook_name: string
  version_number: number
  label: string
  season_code: string
  season_start: string
  season_end: string
  status: string
  target_species: Array<{ code: string; name: string; min_detections: number }>
  requirements: Record<string, any>
  route_segments: Array<Record<string, any>>
  elevation_bands: Array<Record<string, any>>
  notes: string
}

export interface ProbabilityModel {
  id: number
  code: string
  name: string
  version: string
  status: string
  threshold: number
  calibration_sets: Array<{ label: string; pivot_delta: number; n: number }>
  parameters: Record<string, any>
  threshold_review: Record<string, any>
  notes: string
  estimate_count?: number
}

export interface Run {
  id: number
  run_code: string
  label: string
  season_code: string
  season_start: string
  season_end: string
  status: string
  rulebook_version_id: number
  model_version_id: number | null
  created_by: string
  created_at: string
  unit_count: number
  status_counts: Record<string, number>
  open_conflicts: number
  trend_references: number
}

export interface ComparisonRow {
  unit_id: number
  station_code: string
  station_name: string
  date: string
  rule_status: Status
  absence_eligible: boolean
  probability: number | null
  interval: [number, number] | null
  threshold: number | null
  threshold_pass: boolean | null
  divergence: 'both_support' | 'probability_supports_rule_blocks' | 'rule_supports_probability_below'
}

export interface Overview {
  run: Run
  rulebook: RulebookVersion
  model: {
    id: number
    code: string
    version: string
    threshold: number
    status: string
    calibration_sets: Array<Record<string, any>>
  } | null
  unit_count: number
  station_count: number
  date_count: number
  dates: string[]
  status_counts: Record<string, number>
  gate_counts: Record<GateKey, Record<string, number>>
  stations: Array<Station & { units: Array<Record<string, any>> }>
  comparison_rows: ComparisonRow[]
  open_conflicts: number
  active_trend_references: number
  recent_activity: AuditEntry[]
}

export interface Conflict {
  id: number
  unit_id: number
  run_id: number
  material_record_id: number
  material_external_id: string | null
  station_code: string | null
  station_name: string | null
  unit_date: string | null
  species_code: string
  sources: Array<{
    source: string
    analyst: string
    verdict: string
    confidence: number
    determination_id: number
  }>
  detail: string
  status: 'open' | 'resolved'
  resolution: string
  detected_at: string
  resolved_at: string | null
}

export interface AuditEntry {
  id: number
  entity_type: string
  entity_id: number
  action: string
  actor: string
  detail: Record<string, any>
  created_at: string
}

export interface ArchiveRecord {
  id: number
  record_type: string
  record_type_label: string
  external_id: string
  station_id: number
  station_code: string | null
  event_time: string
  received_time: string
  source_device: string
  device_type: string
  latitude: number | null
  longitude: number | null
  coord_precision_m: number | null
  payload: Record<string, any>
  payload_hash: string
  revision: number
  record_status: string
  revisions?: Array<{
    id: number
    revision_number: number
    payload: Record<string, any>
    payload_hash: string
    received_time: string
    changed_by: string
    change_reason: string
    created_at: string
  }>
  determinations?: Determination[]
  used_by_units?: number[]
}

// ------------------------------------------------------- restoration comparison
export interface ComparisonAreaRef {
  id: number
  code: string
  name: string
  role: 'treatment' | 'reference'
  role_label?: string
  boundary: { type: string; coordinates: number[][][] }
  centroid: { lat: number; lon: number }
  elevation_range_m: [number, number]
  habitat: Record<string, any>
  description: string
  stations: Array<{ id: number; code: string; name: string }>
}

export interface ComparisonUnitRow {
  unit_id: number
  station_id: number
  station_code: string | null
  date: string
  certificate_no: string | null
  certificate_status: string
  review_status: string | null
  trend_eligible: boolean
  gate_status: Record<string, string>
  exclusion: string | null
  exclusion_detail: string
  detections: number
  observations: number
  families: Record<string, { detections: number; observations: number; present_species: string[] }>
  effective_effort: {
    available_hours: number
    pending_ratio: number
    observation_quality: number
    effective_effort_days: number
    material_count: number
    calibration_id: string
    note: string
    availability_notes?: Array<Record<string, any>>
  }
  availability_notes: Array<Record<string, any>>
  observation_rate: number
  material_record_ids: number[]
  evidence_record_ids: number[]
  impact_window: Record<string, any> | null
}

export interface ComparisonAreaObservation {
  area_id: number
  code: string
  name: string
  role: string
  station_codes: string[]
  station_ids: number[]
  elevation_range_m: [number, number]
  habitat: Record<string, any>
  units: ComparisonUnitRow[]
  counts: { units: number; usable: number; excluded_impact: number; excluded_other: number }
  expected_dates: string[]
  missing_dates: string[]
  missing_detail: Array<{ date: string; reason: string }>
  construction_windows: Array<Record<string, any>>
}

export interface ComparisonWeight {
  area_id: number
  code: string
  name: string
  status: 'included' | 'excluded'
  weight: number
  reason: string
  common_pre_dates: string[]
  pre_date_coverage: number
  pre_fit_error: number | null
  habitat_similarity: number
  series: { pre: Record<string, number>; candidate: Record<string, number> }
  post_dates?: string[]
}

export interface ComparisonCandidate {
  area_id: number
  code: string
  name: string
  station_codes: string[]
  habitat_similarity: number
  pre_dates: string[]
  post_dates: string[]
  usable_post_dates: string[]
  missing_dates: string[]
  impacted_units: Array<Record<string, any>>
  construction_dates: Array<Record<string, any>>
  impact_note?: string
  construction_note?: string
  heavy_impact?: string
}

export interface ComparisonContribution {
  area_id?: number
  code: string
  weight: number
  rate: number | null
  included: boolean
  contribution?: number
  reason?: string
}

export interface ComparisonResultRow {
  date: string
  phase: string
  sufficient: boolean
  retained_weight: number
  note: string
  treatment_rate: number | null
  reference_rate: number | null
  difference: number | null
  relative_change_pct: number | null
  contributions: ComparisonContribution[]
  treatment_unit_id: number | null
}

export interface ComparisonConclusion {
  key: string
  verdict: string
  basis: string
  not_applicable: string
}

export interface ComparisonAnalysis {
  id: number
  analysis_code: string
  plan_id: number
  plan_code: string | null
  plan_version: number
  status: string
  method: string
  method_label: string
  input_batch: Record<string, any>
  input_snapshot: Record<string, any>
  weights: ComparisonWeight[]
  exclusions: Array<{ area_id: number; code: string; name: string; reason: string; impacted_unit_ids: number[] }>
  results: ComparisonResultRow[]
  summary: Record<string, any>
  change_log: Array<{ source: string; summary: string; detail?: Record<string, any> }>
  previous_analysis_id: number | null
  created_by: string
  created_at: string
  note: string
  frozen: boolean
  areas: ComparisonAreaObservation[]
  candidates: ComparisonCandidate[]
  diagnostics: Record<string, any>
  conclusions: ComparisonConclusion[]
  pre: {
    construction_start: string | null
    construction_end: string | null
    common_pre_dates: string[]
    treatment_pre_dates: string[]
    construction_period_dates?: string[]
    construction_period_note?: string
    post_dates: string[]
    pre_results: Array<Record<string, any>>
    mean_pre_difference: number | null
  }
  post: Record<string, any> | null
  descriptive: Array<{
    area_id: number
    code: string
    name: string
    role: string
    usable_dates: string[]
    unit_count: number
    mean_rate: number | null
    detections: number
    calibrated_effort_days: number
    excluded_dates: Array<Record<string, any>>
  }>
  sensitivity: {
    leave_one_out?: Array<{
      area_id: number
      code: string
      weight: number
      redistributed_weight: number
      per_date: Array<{ date: string; reference_rate: number | null; delta_pct: number | null }>
      max_delta_pct: number
      stable: boolean
    }>
    held_out_pre_dates?: Array<Record<string, any>>
    pre_fit_errors?: Array<Record<string, any>>
  }
  reasons: string[]
  diff?: Record<string, any>
}

export interface ComparisonPlan {
  id: number
  plan_code: string
  name: string
  version: number
  status: string
  season_scope: string[]
  observation_start: string
  observation_end: string
  species_codes: string[]
  criteria: Record<string, any>
  treatment_project_id: number | null
  treatment_project: {
    id: number
    code: string
    name: string
    actual_start: string | null
    actual_end: string | null
    revision: number
  } | null
  created_by: string
  locked_by: string
  locked_at: string | null
  note: string
  members: Array<{ id: number; area_id: number; area_code: string; area_name: string; role: string; note: string }>
  latest_analysis: { id: number; analysis_code: string; status: string; method: string; created_at: string } | null
  missing_reason?: string | null
  analyses?: Array<Record<string, any>>
}

export interface EngineeringProject {
  id: number
  code: string
  name: string
  project_type: string
  status: string
  spatial_boundary: Record<string, any>
  impact_boundary: Record<string, any>
  actual_start: string | null
  actual_end: string | null
  source_type: string
  source_ref: string
  description: string
  revision: number
  created_by: string
  created_at: string | null
  phases: Array<{
    id: number
    phase_code: string
    name: string
    stage_type: string
    planned_start: string | null
    planned_end: string | null
    actual_start: string | null
    actual_end: string | null
    boundary: Record<string, any> | null
    source_type: string
    source_ref: string
    revision: number
    note: string
  }>
  revisions: Array<{
    id: number
    phase_id: number | null
    field: string
    previous: Record<string, any>
    updated: Record<string, any>
    reason: string
    actor: string
    created_at: string
  }>
}
