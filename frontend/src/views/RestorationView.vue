<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { useToast } from '../toast'
import { authState } from '../session'
import type { ComparisonAnalysis, ComparisonPlan, EngineeringProject } from '../types'
import StatusBadge from '../components/StatusBadge.vue'
import { fmtDateTime, fmtNum, fmtPct, GATE_LABEL, statusLabel } from '../format'

const { push } = useToast()

const plan = ref<ComparisonPlan | null>(null)
const project = ref<EngineeringProject | null>(null)
const analysis = ref<ComparisonAnalysis | null>(null)
const history = ref<Array<Record<string, any>>>([])
const loading = ref(true)
const busy = ref('')
const error = ref('')
const tab = ref<'result' | 'weights' | 'areas' | 'sensitivity' | 'engineering' | 'batch'>('result')
const method = ref('weighted_multi_reference')
const note = ref('')

const revisionStart = ref('')
const revisionEnd = ref('')
const revisionNote = ref('')
const phaseForm = ref({
  phase_code: 'P3',
  name: '',
  actual_start: '',
  actual_end: '',
  concurrent: false,
  concurrent_code: '',
  radius_m: 160,
  note: '',
})

const GATE_ORDER = ['device', 'time', 'space', 'reading'] as const
const GATE_STATUS_LABEL: Record<string, string> = {
  pass: '通过',
  restricted: '受限',
  pending: '待核',
  fail: '未满足',
}

const statusLabelMap: Record<string, string> = {
  computed: '已计算',
  descriptive_only: '仅描述性差异',
  under_review: '待复核',
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const plans = await api.get<ComparisonPlan[]>('/comparison/plans')
    plan.value = plans.find((p) => p.plan_code === 'PLAN-WR-2026-01') ?? plans[0] ?? null
    if (!plan.value) throw new Error('尚无比较方案')
    const detail = await api.get<ComparisonPlan>(`/comparison/plans/${plan.value.id}`)
    plan.value = detail
    history.value = detail.analyses ?? []
    if (detail.treatment_project_id) {
      project.value = await api.get<EngineeringProject>(
        `/comparison/projects/${detail.treatment_project_id}`,
      )
      revisionStart.value = project.value.actual_start ?? ''
      revisionEnd.value = project.value.actual_end ?? ''
    }
    const latest = detail.latest_analysis
    if (latest) {
      analysis.value = await api.get<ComparisonAnalysis>(`/comparison/analyses/${latest.id}`)
      method.value = analysis.value.method
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const canWrite = computed(() => authState.isWriter.value)
const treatment = computed(() => analysis.value?.areas.find((a) => a.role === 'treatment') ?? null)
const references = computed(
  () => analysis.value?.areas.filter((a) => a.role !== 'treatment') ?? [],
)
const includedWeights = computed(
  () => analysis.value?.weights.filter((w) => w.status === 'included') ?? [],
)
const excludedWeights = computed(
  () => analysis.value?.weights.filter((w) => w.status === 'excluded') ?? [],
)
const maxWeight = computed(() =>
  Math.max(0.01, ...(analysis.value?.weights ?? []).map((w) => w.weight)),
)
const preRows = computed(() => analysis.value?.pre.pre_results ?? [])
const postRows = computed(() => analysis.value?.results ?? [])

const headline = computed(() => {
  const a = analysis.value
  if (!a) return ''
  if (a.status === 'under_review') return '比较结论待复核：剩余证据不足以维持原方案'
  if (a.status === 'descriptive_only') return '仅报告描述性差异：修复前共同窗口或修复后证据不足'
  const mean = a.post?.mean_difference
  if (mean === null || mean === undefined) return '已计算，但修复后差异尚不可报告'
  const dir = mean > 0 ? '高于' : mean < 0 ? '低于' : '接近'
  return `修复后处理区校正观测率平均${dir}参照组合 ${Math.abs(mean).toFixed(3)}/有效相机日`
})

function statusClass(status: string): string {
  if (status === 'computed') return 'valid'
  if (status === 'under_review') return 'under_review'
  return 'restricted'
}

function exclusionLabel(code: string | null): string {
  const map: Record<string, string> = {
    construction_impact: '施工波及',
    no_certificate: '无证书',
    under_review: '复审中',
    certificate_not_comparable: '证书不可比',
  }
  return code ? map[code] ?? code : ''
}

async function runAnalysis(mode: 'analyse' | 'recompute') {
  if (!plan.value) return
  busy.value = mode
  try {
    const payload = { method: method.value, note: note.value }
    const result =
      mode === 'analyse'
        ? await api.post<ComparisonAnalysis>(`/comparison/plans/${plan.value.id}/analyse`, payload)
        : await api.post<ComparisonAnalysis>(`/comparison/plans/${plan.value.id}/recompute`, {
            note: note.value,
          })
    analysis.value = result
    push(mode === 'analyse' ? '已生成新的比较报告' : '已按当前输入重新计算，旧报告保留')
    await load()
  } catch (err) {
    push(err instanceof Error ? err.message : '计算失败', 'error')
  } finally {
    busy.value = ''
  }
}

async function openAnalysis(id: number) {
  try {
    analysis.value = await api.get<ComparisonAnalysis>(`/comparison/analyses/${id}`)
    tab.value = 'result'
  } catch (err) {
    push(err instanceof Error ? err.message : '加载失败', 'error')
  }
}

async function reviseDates() {
  if (!project.value) return
  if (!revisionNote.value.trim()) {
    push('请填写修订原因', 'error')
    return
  }
  if (revisionStart.value && revisionEnd.value && revisionEnd.value < revisionStart.value) {
    push('实际完工日期不能早于实际开工日期', 'error')
    return
  }
  busy.value = 'revision'
  try {
    await api.post(`/comparison/projects/${project.value.id}/revisions`, {
      actual_start: revisionStart.value || null,
      actual_end: revisionEnd.value || null,
      note: revisionNote.value,
    })
    push('工程日期已修订（保留修订记录），请重新计算')
    revisionNote.value = ''
    await load()
  } catch (err) {
    push(err instanceof Error ? err.message : '修订失败', 'error')
  } finally {
    busy.value = ''
  }
}

function circleBoundary(lat: number, lon: number, radiusM: number) {
  // Small circle approximated as a square ring; the backend only needs a polygon.
  const dLat = radiusM / 110540
  const dLon = radiusM / (111320 * Math.cos((lat * Math.PI) / 180))
  const ring = [
    [lon - dLon, lat - dLat],
    [lon + dLon, lat - dLat],
    [lon + dLon, lat + dLat],
    [lon - dLon, lat + dLat],
    [lon - dLon, lat - dLat],
  ]
  return { type: 'Polygon', coordinates: [ring] }
}

async function addPhase() {
  if (!project.value) return
  if (!phaseForm.value.actual_start) {
    push('请填写实际开工日期', 'error')
    return
  }
  busy.value = 'phase'
  try {
    const lat = 31.205
    await api.post(`/comparison/projects/${project.value.id}/phases`, {
      phase_code: phaseForm.value.phase_code,
      name: phaseForm.value.name || '新增施工阶段',
      actual_start: phaseForm.value.actual_start,
      actual_end: phaseForm.value.actual_end || null,
      boundary: circleBoundary(lat, 103.755, phaseForm.value.radius_m),
      source_type: '施工月报',
      source_ref: '界面录入',
      note: phaseForm.value.note || '录入施工阶段',
      concurrent_project_code: phaseForm.value.concurrent ? phaseForm.value.concurrent_code : null,
      concurrent_project_name: phaseForm.value.concurrent ? phaseForm.value.name : null,
    })
    push('已登记施工阶段；重新计算后会重新确定可比单元')
    phaseForm.value.name = ''
    phaseForm.value.concurrent_code = ''
    await load()
  } catch (err) {
    push(err instanceof Error ? err.message : '登记失败', 'error')
  } finally {
    busy.value = ''
  }
}

function unitRowClass(row: Record<string, any>): string {
  return row.exclusion ? 'unit-excluded' : ''
}

function gatePairs(row: Record<string, any>): Array<{ gate: string; label: string; status: string }> {
  const statuses = row.gate_status ?? {}
  return GATE_ORDER.map((gate) => ({
    gate,
    label: GATE_LABEL[gate] ?? gate,
    status: statuses[gate] ?? '—',
  }))
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Restoration Comparison Workspace</div>
        <h1>湿地修复调查数据比较</h1>
        <p class="page-sub">
          比较修复区与候选参照区的调查观测差异，说明哪些差异有可比依据、哪些尚不足以支持工程影响判断。
          权重只由修复前合格调查拟合，有效努力量先按一致物种与季节校准；施工影响按实际范围剔除，
          不因行政归属认定未受影响。本工作区不执行工程审批，也不对项目自动评优。
        </p>
      </div>
      <div class="toolbar" v-if="plan">
        <span class="chip mono">{{ plan.plan_code }} v{{ plan.version }}</span>
        <span class="chip">{{ plan.status === 'locked' ? '方案已锁定' : '方案草稿' }}</span>
      </div>
    </div>

    <div v-if="loading" class="loading">加载比较工作区…</div>
    <div v-else-if="error" class="callout danger">{{ error }}</div>

    <template v-else-if="plan">
      <div class="grid grid-4" style="margin-bottom: 14px">
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">{{ treatment?.counts.usable ?? 0 }}</div>
            <div class="stat-label">处理区合格调查单元</div>
            <div class="stat-hint">
              {{ treatment?.station_codes.join('、') }} · 剔除 {{ treatment?.counts.excluded_impact ?? 0 }} 个施工波及
            </div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">{{ includedWeights.length }}</div>
            <div class="stat-label">进入组合的参照区</div>
            <div class="stat-hint">另有 {{ excludedWeights.length }} 个候选按纳入依据排除</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">{{ postRows.filter((r) => r.sufficient).length }} / {{ postRows.length }}</div>
            <div class="stat-label">可报告的修复后比较日</div>
            <div class="stat-hint">权重保留不足的日期暂停报告</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">
              <StatusBadge
                v-if="analysis"
                :status="statusClass(analysis.status)"
                :label="statusLabelMap[analysis.status] ?? analysis.status"
              />
              <span v-else>—</span>
            </div>
            <div class="stat-label">当前报告状态</div>
            <div class="stat-hint" v-if="analysis">{{ analysis.analysis_code }} · {{ analysis.method_label }}</div>
          </div>
        </div>
      </div>

      <div class="callout" :class="analysis?.status === 'under_review' ? 'warn' : analysis?.status === 'descriptive_only' ? 'info' : 'pine'" style="margin-bottom: 14px">
        <div class="callout-title">{{ headline }}</div>
        <div class="small" v-if="analysis">
          工程实际施工 {{ analysis.pre.construction_start ?? '—' }} → {{ analysis.pre.construction_end ?? '—' }}；
          修复前共同窗口 {{ analysis.pre.common_pre_dates.join('、') || '无' }}；
          修复后比较日 {{ (analysis.post?.reported_dates ?? []).join('、') || '无' }}。
          <span v-if="(analysis.pre.construction_period_dates ?? []).length" style="color: var(--amber)">
            施工期调查日 {{ (analysis.pre.construction_period_dates ?? []).join('、') }} 已排除（既非基线也非结果）。
          </span>
        </div>
        <ul v-if="analysis?.reasons.length" class="reason-list">
          <li v-for="(reason, idx) in analysis.reasons" :key="idx">{{ reason }}</li>
        </ul>
      </div>

      <div class="tabs">
        <button class="tab" :class="{ active: tab === 'result' }" @click="tab = 'result'">比较结果</button>
        <button class="tab" :class="{ active: tab === 'weights' }" @click="tab = 'weights'">权重与候选</button>
        <button class="tab" :class="{ active: tab === 'areas' }" @click="tab = 'areas'">区域观测与缺失</button>
        <button class="tab" :class="{ active: tab === 'sensitivity' }" @click="tab = 'sensitivity'">敏感性与适用范围</button>
        <button class="tab" :class="{ active: tab === 'engineering' }" @click="tab = 'engineering'">工程资料维护</button>
        <button class="tab" :class="{ active: tab === 'batch' }" @click="tab = 'batch'">输入批次与报告历史</button>
      </div>

      <!-- ---------------------------------------------------------- result -->
      <template v-if="tab === 'result'">
        <div class="grid grid-2-1">
          <div class="card">
            <div class="card-head">
              <h2>修复前基线 vs 修复后差异</h2>
              <span class="chip" v-if="analysis">观测率单位：次/有效相机日</span>
            </div>
            <div v-if="!analysis" class="empty">尚未计算。生成报告后可查看比较结果。</div>
            <template v-else>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>日期</th>
                      <th>阶段</th>
                      <th class="num">处理区</th>
                      <th class="num">参照组合</th>
                      <th class="num">差异</th>
                      <th>说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in preRows" :key="'pre-' + row.date">
                      <td class="mono tiny">{{ row.date }}</td>
                      <td><span class="badge neutral">修复前</span></td>
                      <td class="num mono">{{ fmtNum(row.treatment_rate, 3) }}</td>
                      <td class="num mono">{{ fmtNum(row.reference_rate, 3) }}</td>
                      <td class="num mono">{{ fmtNum(row.difference, 3) }}</td>
                      <td class="tiny muted">用于拟合权重，不解释为工程影响</td>
                    </tr>
                    <tr v-for="row in postRows" :key="'post-' + row.date" :class="{ 'row-paused': !row.sufficient }">
                      <td class="mono tiny">{{ row.date }}</td>
                      <td>
                        <span class="badge" :class="row.sufficient ? 'pending' : 'invalid'">
                          {{ row.sufficient ? '修复后' : '暂停' }}
                        </span>
                      </td>
                      <td class="num mono">{{ fmtNum(row.treatment_rate, 3) }}</td>
                      <td class="num mono">{{ fmtNum(row.reference_rate, 3) }}</td>
                      <td class="num mono">
                        <span v-if="row.difference !== null">{{ row.difference > 0 ? '+' : '' }}{{ fmtNum(row.difference, 3) }}</span>
                        <span v-else class="muted">暂停</span>
                      </td>
                      <td class="tiny" :class="row.sufficient ? 'muted' : ''" style="color: row.sufficient ? undefined : 'var(--rust); max-width: 260px'">
                        {{ row.sufficient ? `权重保留 ${fmtPct(row.retained_weight)}` : row.note }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="callout info" style="margin-top: 12px" v-if="analysis.post">
                修复后可报告日期：{{ (analysis.post.reported_dates ?? []).join('、') || '无' }}；
                暂停日期：{{ (analysis.post.paused_dates ?? []).join('、') || '无' }}。
                所有差异均为观测比较，不表述为工程有效性结论。
              </div>
            </template>
          </div>

          <div class="stack">
            <div class="card">
              <div class="card-head"><h2>结论与不适用范围</h2></div>
              <div v-if="!analysis" class="empty">尚无报告。</div>
              <div v-else class="stack">
                <div v-for="item in analysis.conclusions" :key="item.key" class="concl">
                  <div class="concl-verdict">{{ item.verdict }}</div>
                  <div class="tiny muted">依据：{{ item.basis }}</div>
                  <div class="concl-scope">不适用于：{{ item.not_applicable }}</div>
                </div>
              </div>
            </div>
            <div class="card">
              <div class="card-head"><h2>操作</h2></div>
              <div class="stack-sm">
                <div class="field">
                  <label>比较方法</label>
                  <select v-model="method" class="select">
                    <option value="weighted_multi_reference">多参照加权（默认）</option>
                    <option value="single_reference">单参照</option>
                    <option value="descriptive_only">仅描述性差异</option>
                  </select>
                </div>
                <div class="field">
                  <label>说明（写入报告）</label>
                  <input v-model="note" class="input" placeholder="例如：补齐参照区调查后重算" />
                </div>
                <div class="toolbar">
                  <button class="btn primary" :disabled="!canWrite || !!busy" @click="runAnalysis('analyse')">
                    {{ busy === 'analyse' ? '计算中…' : '生成新报告' }}
                  </button>
                  <button class="btn" :disabled="!canWrite || !!busy" @click="runAnalysis('recompute')">
                    {{ busy === 'recompute' ? '重算中…' : '按当前输入重算' }}
                  </button>
                </div>
                <div class="tiny muted">
                  重算会新增一份报告并链到上一份；旧报告保留当时证据与结论，不会被改写。
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="card" style="margin-top: 14px" v-if="analysis?.diff && analysis.diff.source">
          <div class="card-head">
            <h2>本次变化来自哪里</h2>
            <span class="chip mono">{{ analysis.diff.source }}</span>
          </div>
          <div class="grid grid-3">
            <div>
              <div class="tiny muted">输入修订 vs 方案变化</div>
              <div class="small">
                工程输入：{{ analysis.diff.engineering_input_changed ? '有修订' : '未变' }} ·
                调查输入：{{ analysis.diff.survey_input_changed ? '有修订' : '未变' }} ·
                方案：{{ analysis.diff.plan_signature?.previous !== analysis.diff.plan_signature?.current ? '已变更' : '未变' }}
              </div>
            </div>
            <div>
              <div class="tiny muted">退出/新增的可比单元</div>
              <div class="small">
                退出 {{ (analysis.diff.units_removed ?? []).length }} 个 · 新增
                {{ (analysis.diff.units_added ?? []).length }} 个
              </div>
              <div class="tiny muted mono" v-for="u in (analysis.diff.units_removed ?? []).slice(0, 4)" :key="u">
                − {{ u }}
              </div>
              <div class="tiny muted mono" v-for="u in (analysis.diff.units_added ?? []).slice(0, 4)" :key="u">
                + {{ u }}
              </div>
            </div>
            <div>
              <div class="tiny muted">权重变化</div>
              <div class="small" v-for="w in analysis.diff.weight_changes ?? []" :key="w.code">
                {{ w.code }}：{{ fmtPct(w.previous) }} → {{ fmtPct(w.current) }}
              </div>
              <div class="small muted" v-if="!(analysis.diff.weight_changes ?? []).length">权重未变</div>
            </div>
          </div>
          <div class="callout warn" style="margin-top: 10px" v-if="(analysis.diff.paused ?? []).length">
            <div class="callout-title">结论暂停</div>
            <div class="small" v-for="(p, idx) in analysis.diff.paused" :key="idx">
              {{ p.reference ?? '整体比较' }}：{{ p.reason }}
              <span class="mono tiny" v-if="(p.affected_unit_ids ?? []).length">
                （单元 {{ p.affected_unit_ids.slice(0, 4).join('、') }}）
              </span>
            </div>
          </div>
          <div class="callout info" style="margin-top: 10px" v-if="(analysis.diff.reference_exits ?? []).length && !(analysis.diff.paused ?? []).length">
            参照退出但剩余参照足以维持比较，结论按新输入重算：
            <span v-for="e in analysis.diff.reference_exits" :key="e.reference" class="chip" style="margin-right: 4px">
              {{ e.reference }}（{{ (e.affected_dates ?? []).join('、') || '修复前证据不足' }}）
            </span>
          </div>
        </div>
      </template>

      <!-- --------------------------------------------------------- weights -->
      <template v-else-if="tab === 'weights'">
        <div class="grid grid-2">
          <div class="card">
            <div class="card-head">
              <h2>候选参照的贡献与排除理由</h2>
              <span class="chip" v-if="analysis">权重仅用修复前共同窗口拟合</span>
            </div>
            <div v-if="!analysis" class="empty">尚无报告。</div>
            <template v-else>
              <div v-for="w in analysis.weights" :key="w.code" class="weight-row" :class="{ excluded: w.status === 'excluded' }">
                <div class="weight-head">
                  <div>
                    <span class="mono" style="font-weight: 600">{{ w.code }}</span>
                    <span class="small" style="margin-left: 6px">{{ w.name }}</span>
                  </div>
                  <div class="toolbar">
                    <span class="chip">生境相似 {{ fmtNum(w.habitat_similarity, 2) }}</span>
                    <span class="chip" v-if="w.pre_fit_error !== null">修复前拟合误差 {{ fmtNum(w.pre_fit_error, 3) }}</span>
                    <span class="chip">共同窗口 {{ w.common_pre_dates.length }} 日</span>
                    <span class="badge" :class="w.status === 'included' ? 'valid' : 'neutral'">
                      {{ w.status === 'included' ? `权重 ${fmtPct(w.weight)}` : '已排除' }}
                    </span>
                  </div>
                </div>
                <div class="weight-bar">
                  <div :style="{ width: (w.weight / maxWeight) * 100 + '%' }"></div>
                </div>
                <div class="tiny muted" v-if="w.status === 'excluded'">{{ w.reason }}</div>
                <div class="tiny muted" v-else>
                  共同修复前日期：{{ w.common_pre_dates.join('、') }}；
                  修复前覆盖率 {{ fmtPct(w.pre_date_coverage) }}。
                </div>
                <div class="pill-row" style="margin-top: 4px" v-if="(w.post_dates ?? []).length">
                  <span class="chip" v-for="d in w.post_dates" :key="d">修复后可用 {{ d }}</span>
                </div>
              </div>
              <div class="divider"></div>
              <div class="callout" :class="analysis.diagnostics.weight_stability === 'unstable' ? 'warn' : 'pine'">
                权重诊断：{{ analysis.diagnostics.weight_stability ?? '—' }}
                · 极差 {{ analysis.diagnostics.weight_spread_pct ?? '—' }}%
                · 修复后可保留权重最低 {{ fmtPct(analysis.diagnostics.retained_weight_share) }}
              </div>
            </template>
          </div>

          <div class="card">
            <div class="card-head"><h2>每个候选的排除/剔除原因</h2></div>
            <div v-if="!analysis" class="empty">尚无报告。</div>
            <template v-else>
              <div v-if="!analysis.exclusions.length" class="empty">没有候选被排除。</div>
              <div class="stack-sm" v-else>
                <div v-for="e in analysis.exclusions" :key="e.code" class="exclusion-row">
                  <div class="between">
                    <span class="mono small">{{ e.code }}</span>
                    <span class="badge neutral">{{ e.name }}</span>
                  </div>
                  <div class="tiny" style="color: var(--rust)">{{ e.reason }}</div>
                </div>
              </div>
              <div class="divider"></div>
              <div class="stack-sm">
                <div v-for="c in analysis.candidates.filter((c) => c.impacted_units.length || c.construction_dates.length || c.heavy_impact)" :key="c.code">
                  <div class="between">
                    <span class="mono small">{{ c.code }}</span>
                    <span class="chip">{{ c.impacted_units.length }} 个波及单元</span>
                  </div>
                  <div class="tiny muted">{{ c.impact_note || c.construction_note || c.heavy_impact }}</div>
                  <div class="tiny mono" v-for="u in c.impacted_units.slice(0, 3)" :key="u.unit_id">
                    {{ u.date }} · {{ u.certificate_no }} · {{ u.impact_window?.phase_code ?? '施工期' }}
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </template>

      <!-- ----------------------------------------------------------- areas -->
      <template v-else-if="tab === 'areas'">
        <div v-if="!analysis" class="card"><div class="empty">尚无报告。</div></div>
        <div v-else class="stack">
          <div v-for="area in analysis.areas" :key="area.code" class="card">
            <div class="card-head">
              <h2>
                {{ area.code }} · {{ area.name }}
                <span class="badge" :class="area.role === 'treatment' ? 'valid' : 'neutral'">
                  {{ area.role === 'treatment' ? '处理区' : '参照区' }}
                </span>
              </h2>
              <div class="toolbar">
                <span class="chip">{{ area.station_codes.join('、') }}</span>
                <span class="chip">合格 {{ area.counts.usable }} / {{ area.counts.units }}</span>
                <span class="chip" v-if="area.missing_dates.length">{{ area.missing_dates.length }} 个缺失调查日</span>
              </div>
            </div>
            <div class="callout warn" style="margin-bottom: 10px" v-if="area.missing_dates.length">
              <div class="callout-title">部分季节调查缺失（不计为 0 次观测）</div>
              <div class="small">{{ area.missing_dates.join('、') }}；这些日期没有合格调查单元，未作为零观测参与计算。</div>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>证书</th>
                    <th>门槛</th>
                    <th class="num">检出</th>
                    <th class="num">有效努力量（相机日）</th>
                    <th class="num">观测率</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="u in area.units" :key="u.unit_id" :class="unitRowClass(u)">
                    <td class="mono tiny">{{ u.date }}</td>
                    <td class="tiny">
                      <div class="mono">{{ u.certificate_no ?? '—' }}</div>
                      <div class="muted">
                        {{ statusLabel(u.certificate_status, u.review_status) }}
                      </div>
                    </td>
                    <td class="tiny">
                      <div class="gate-mini">
                        <span v-for="g in gatePairs(u)" :key="g.gate" class="chip" :title="g.label">
                          {{ g.label }} {{ GATE_STATUS_LABEL[g.status] ?? g.status }}
                        </span>
                      </div>
                    </td>
                    <td class="num mono">{{ u.detections }}</td>
                    <td class="num mono">
                      {{ fmtNum(u.effective_effort.effective_effort_days, 2) }}
                      <div class="tiny muted">可用 {{ fmtNum(u.effective_effort.available_hours, 1) }}h · 判读完成 {{ fmtPct(u.effective_effort.observation_quality) }}</div>
                    </td>
                    <td class="num mono">{{ fmtNum(u.observation_rate, 3) }}</td>
                    <td class="tiny">
                      <span v-if="!u.exclusion" class="badge valid">纳入</span>
                      <span v-else class="badge invalid">{{ exclusionLabel(u.exclusion) }}</span>
                      <div class="muted" v-if="u.exclusion_detail" style="max-width: 320px">{{ u.exclusion_detail }}</div>
                      <div class="muted" v-if="(u.availability_notes ?? []).length">
                        已按可用性说明折算：{{ u.availability_notes.map((n: any) => n.reason).join('；') }}
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="tiny muted" v-if="area.construction_windows.length" style="margin-top: 8px">
              覆盖本区域的施工窗口：
              <span v-for="w in area.construction_windows" :key="w.project_code + (w.phase_code ?? '')" class="chip" style="margin-right: 4px">
                {{ w.project_code }} {{ w.phase_code ?? '' }} {{ w.start }}→{{ w.end ?? '进行中' }}
              </span>
            </div>
          </div>
        </div>
      </template>

      <!-- ----------------------------------------------------- sensitivity -->
      <template v-else-if="tab === 'sensitivity'">
        <div v-if="!analysis" class="card"><div class="empty">尚无报告。</div></div>
        <template v-else>
          <div class="grid grid-2">
            <div class="card">
              <div class="card-head"><h2>修复前拟合误差与留出时间段</h2></div>
              <div v-if="!(analysis.sensitivity.pre_fit_errors ?? []).length" class="empty">没有可展示的修复前拟合。</div>
              <div class="table-wrap" v-else>
                <table>
                  <thead>
                    <tr><th>参照</th><th class="num">权重</th><th class="num">修复前拟合误差</th><th class="num">覆盖率</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in analysis.sensitivity.pre_fit_errors" :key="row.code">
                      <td class="mono small">{{ row.code }}</td>
                      <td class="num mono">{{ fmtPct(row.weight) }}</td>
                      <td class="num mono">{{ fmtNum(row.pre_fit_error, 3) }}</td>
                      <td class="num mono">{{ fmtPct(row.pre_date_coverage) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="divider"></div>
              <div class="tiny muted">留出修复前日期检验（拟合时排除该日，再用该日比较）</div>
              <div class="stack-sm" style="margin-top: 6px">
                <div v-for="row in analysis.sensitivity.held_out_pre_dates ?? []" :key="row.held_out_date" class="between small">
                  <span class="mono">{{ row.held_out_date }}</span>
                  <span>参照 {{ row.reference_code }} · 拟合误差 {{ fmtNum(row.fit_error, 3) }}</span>
                  <span class="mono">留出差异 {{ fmtNum(row.observed_difference, 3) }}</span>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-head"><h2>去掉单个参照后的敏感性</h2></div>
              <div v-if="!(analysis.sensitivity.leave_one_out ?? []).length" class="empty">没有可展示的去一参照检验。</div>
              <template v-else>
                <div v-for="row in analysis.sensitivity.leave_one_out" :key="row.code" class="loo-row">
                  <div class="between">
                    <span class="mono small">去掉 {{ row.code }}（原权重 {{ fmtPct(row.weight) }}）</span>
                    <span class="badge" :class="row.stable ? 'valid' : 'restricted'">
                      {{ row.stable ? '稳定' : '敏感' }} · 最大变化 {{ fmtPct(row.max_delta_pct / 100) }}
                    </span>
                  </div>
                  <div class="tiny muted">
                    <span v-for="d in row.per_date" :key="d.date">
                      {{ d.date }}：{{ d.delta_pct === null ? '暂停' : `${d.delta_pct > 0 ? '+' : ''}${d.delta_pct.toFixed(1)}%` }}
                    </span>
                  </div>
                </div>
              </template>
            </div>
          </div>

          <div class="card" style="margin-top: 14px">
            <div class="card-head"><h2>描述性差异（不依赖拟合）</h2></div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>区域</th><th>角色</th><th class="num">合格单元</th>
                    <th class="num">平均观测率</th><th class="num">检出</th>
                    <th class="num">校准努力量</th><th>合格日期</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in analysis.descriptive" :key="row.code">
                    <td class="mono small">{{ row.code }}</td>
                    <td class="small">{{ row.role === 'treatment' ? '处理区' : '参照区' }}</td>
                    <td class="num mono">{{ row.unit_count }}</td>
                    <td class="num mono">{{ fmtNum(row.mean_rate, 3) }}</td>
                    <td class="num mono">{{ row.detections }}</td>
                    <td class="num mono">{{ fmtNum(row.calibrated_effort_days, 2) }}</td>
                    <td class="tiny muted">{{ row.usable_dates.join('、') }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>
      </template>

      <!-- ------------------------------------------------------ engineering -->
      <template v-else-if="tab === 'engineering'">
        <div class="grid grid-2-1">
          <div class="card" v-if="project">
            <div class="card-head">
              <h2>工程资料</h2>
              <span class="chip mono">修订 {{ project.revision }}</span>
            </div>
            <div class="kv">
              <dt>工程编号</dt><dd class="mono">{{ project.code }}</dd>
              <dt>名称</dt><dd>{{ project.name }}</dd>
              <dt>实际施工</dt><dd>{{ project.actual_start ?? '—' }} → {{ project.actual_end ?? '进行中' }}</dd>
              <dt>来源</dt><dd>{{ project.source_type }} · <span class="mono">{{ project.source_ref }}</span></dd>
              <dt>说明</dt><dd class="small">{{ project.description }}</dd>
            </div>
            <div class="divider"></div>
            <h3 style="margin-bottom: 8px">阶段与空间范围</h3>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr><th>阶段</th><th>名称</th><th>实际日期</th><th>来源</th><th class="num">修订</th></tr>
                </thead>
                <tbody>
                  <tr v-for="p in project.phases" :key="p.id">
                    <td class="mono small">{{ p.phase_code }}</td>
                    <td class="small">{{ p.name }}</td>
                    <td class="small mono">{{ p.actual_start ?? '—' }} → {{ p.actual_end ?? '—' }}</td>
                    <td class="tiny">{{ p.source_type }} · {{ p.source_ref }}</td>
                    <td class="num mono">{{ p.revision }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="divider"></div>
            <h3 style="margin-bottom: 8px">修订记录（补录、日期更正、影响范围扩大）</h3>
            <div v-if="!project.revisions.length" class="empty">尚无修订记录。</div>
            <div class="stack-sm" v-else>
              <div v-for="r in project.revisions" :key="r.id" class="revision-row">
                <div class="between">
                  <span class="mono small">{{ r.field }}</span>
                  <span class="tiny muted">{{ fmtDateTime(r.created_at) }} · {{ r.actor }}</span>
                </div>
                <div class="tiny muted">{{ r.reason }}</div>
                <div class="pre tiny">{{ JSON.stringify({ previous: r.previous, updated: r.updated }, null, 1) }}</div>
              </div>
            </div>
          </div>

          <div class="stack">
            <div class="card">
              <div class="card-head"><h2>更正实际施工日期</h2></div>
              <div class="stack-sm">
                <div class="field">
                  <label>实际开工</label>
                  <input v-model="revisionStart" type="date" class="input" :disabled="!canWrite" />
                </div>
                <div class="field">
                  <label>实际完工</label>
                  <input v-model="revisionEnd" type="date" class="input" :disabled="!canWrite" />
                </div>
                <div class="field">
                  <label>修订原因（必填，写入修订记录）</label>
                  <input v-model="revisionNote" class="input" placeholder="例如：工程开始日期回溯更正" :disabled="!canWrite" />
                </div>
                <button class="btn primary" :disabled="!canWrite || busy === 'revision'" @click="reviseDates">
                  {{ busy === 'revision' ? '保存中…' : '保存修订' }}
                </button>
                <div class="tiny muted">修订后需“按当前输入重算”，变化会标注为输入修订，与方案变化分开记录。</div>
              </div>
            </div>

            <div class="card">
              <div class="card-head"><h2>登记施工阶段 / 同期施工</h2></div>
              <div class="stack-sm">
                <div class="field"><label>阶段编号</label><input v-model="phaseForm.phase_code" class="input" /></div>
                <div class="field"><label>名称</label><input v-model="phaseForm.name" class="input" placeholder="例如：参照区外围施工" /></div>
                <div class="grid grid-2">
                  <div class="field"><label>实际开工</label><input v-model="phaseForm.actual_start" type="date" class="input" /></div>
                  <div class="field"><label>实际完工（可空）</label><input v-model="phaseForm.actual_end" type="date" class="input" /></div>
                </div>
                <div class="field">
                  <label>影响范围半径（米，近似方形边界）</label>
                  <input v-model.number="phaseForm.radius_m" type="number" min="20" class="input" />
                </div>
                <label class="small flex" style="gap: 6px">
                  <input v-model="phaseForm.concurrent" type="checkbox" />
                  作为同期独立工程登记（参照区自身开工，不改处理区日期）
                </label>
                <div class="field" v-if="phaseForm.concurrent">
                  <label>同期工程编号</label>
                  <input v-model="phaseForm.concurrent_code" class="input" placeholder="例如：WR-2026-R1" />
                </div>
                <div class="field"><label>备注</label><input v-model="phaseForm.note" class="input" /></div>
                <button class="btn primary" :disabled="!canWrite || busy === 'phase'" @click="addPhase">
                  {{ busy === 'phase' ? '保存中…' : '登记阶段' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- ------------------------------------------------------------ batch -->
      <template v-else>
        <div class="grid grid-2">
          <div class="card">
            <div class="card-head"><h2>报告历史（冻结，不追溯改写）</h2></div>
            <div v-if="!history.length" class="empty">尚无报告。</div>
            <div v-else class="stack-sm">
              <button
                v-for="row in history"
                :key="row.id"
                class="history-row"
                :class="{ active: analysis?.id === row.id }"
                @click="openAnalysis(row.id)"
              >
                <span class="mono small">{{ row.analysis_code }}</span>
                <span class="badge" :class="statusClass(row.status)">{{ statusLabelMap[row.status] ?? row.status }}</span>
                <span class="tiny muted">{{ row.created_by }} · {{ fmtDateTime(row.created_at) }}</span>
              </button>
            </div>
          </div>

          <div class="card" v-if="analysis">
            <div class="card-head"><h2>输入批次</h2></div>
            <div class="kv">
              <dt>输入批次号</dt><dd class="mono">{{ analysis.analysis_code }}</dd>
              <dt>方案</dt><dd class="mono">{{ analysis.plan_code }} v{{ analysis.plan_version }}</dd>
              <dt>调查指纹</dt><dd class="mono">{{ analysis.input_batch.survey_fingerprint }}</dd>
              <dt>工程输入指纹</dt><dd class="mono">{{ analysis.input_batch.engineering?.fingerprint }}</dd>
              <dt>方案签名</dt><dd class="mono">{{ analysis.input_batch.plan_signature }}</dd>
              <dt>观察窗口</dt><dd class="mono">{{ (analysis.input_batch.observation_window ?? []).join(' → ') }}</dd>
              <dt>物种范围</dt><dd class="mono">{{ (analysis.input_batch.species_codes ?? []).join('、') }}</dd>
              <dt>上份报告</dt><dd class="mono">{{ analysis.previous_analysis_id ?? '首次' }}</dd>
            </div>
            <div class="divider"></div>
            <div class="callout info">
              结果引用确定的方案版本与输入批次；每个差异可逐项追到具体调查单元与证书。
              旧报告保留当时证据，新修订不覆盖历史。
            </div>
            <div class="divider"></div>
            <h3 style="margin-bottom: 6px">本批次纳入/剔除单元（前 14 条）</h3>
            <div class="table-wrap" style="max-height: 280px; overflow-y: auto">
              <table>
                <thead><tr><th>单元</th><th>证书</th><th>纳入</th><th class="num">观测率</th></tr></thead>
                <tbody>
                  <tr v-for="u in (analysis.input_batch.units ?? []).slice(0, 14)" :key="u.unit_key">
                    <td class="mono tiny">{{ u.unit_key }}</td>
                    <td class="tiny mono">{{ u.certificate_no ?? '—' }}</td>
                    <td class="tiny">
                      <span class="badge" :class="u.included ? 'valid' : 'invalid'">{{ u.included ? '纳入' : exclusionLabel(u.exclusion) }}</span>
                    </td>
                    <td class="num mono">{{ fmtNum(u.observation_rate, 3) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.reason-list { margin: 6px 0 0 16px; padding: 0; font-size: 12px; }
.reason-list li { margin-bottom: 2px; }
.row-paused { background: #fbf3f1; }
.concl { border-left: 3px solid var(--pine-mid); padding: 6px 10px; background: var(--surface-2); border-radius: 6px; }
.concl-verdict { font-size: 12.5px; font-weight: 600; }
.concl-scope { font-size: 11.5px; color: var(--amber); margin-top: 3px; }

.weight-row { padding: 10px 0; border-bottom: 1px dashed var(--line); }
.weight-row:last-child { border-bottom: none; }
.weight-row.excluded { opacity: 0.78; }
.weight-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
.weight-bar { height: 8px; border-radius: 4px; background: var(--surface-2); overflow: hidden; margin-bottom: 4px; }
.weight-bar div { height: 100%; background: linear-gradient(90deg, #8fb9a3, var(--pine-mid)); }
.weight-row.excluded .weight-bar div { background: var(--line-strong); }

.exclusion-row { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; background: #fff; }
.loo-row { padding: 8px 0; border-bottom: 1px dashed var(--line); }
.loo-row:last-child { border-bottom: none; }
.loo-row .tiny span { margin-right: 10px; }

.unit-excluded { background: #fbf3f1; }
.gate-mini { display: flex; flex-direction: column; gap: 2px; }
.revision-row { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
.revision-row .pre { max-height: 120px; overflow-y: auto; }

.history-row {
  display: flex; align-items: center; gap: 10px; width: 100%;
  border: 1px solid var(--line); border-radius: 8px; background: #fff;
  padding: 8px 10px; cursor: pointer; text-align: left;
}
.history-row:hover { background: var(--surface-2); }
.history-row.active { border-color: var(--pine-mid); background: var(--pine-soft); }
</style>
