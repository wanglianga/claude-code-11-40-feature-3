# 自然保护区季节调查完整性与覆盖数据服务

面向自然保护区研究人员的**覆盖与调查有效性数据服务**：按「站点—样区—日期」组装调查单元，
逐项判断设备可用性、时间可信度、空间覆盖与素材判读是否满足规则册门槛，签发
**有效调查 / 受限调查 / 未有效调查 / 待核**证书，并并列展示经历史校准的检测概率估计。

> 服务只交付覆盖与调查有效性数据。它**不派发巡护任务、不预约人员器材、不创建执法/捕捉/资源调度动作**，
> 也不把调查缺口转成行动指令。
>
> 另含**湿地修复调查数据比较工作区**：为修复区选择候选参照区，说明哪些观测差异有可比依据、
> 哪些尚不足以支持工程影响判断（权重只用修复前合格调查拟合；施工影响按工程实际范围与日期剔除，
> 不按行政归属认定未受影响；没有足够修复前共同窗口时只报告描述性差异）。
> 该工作区**不执行工程审批，不对项目自动评优**。

---

## 1. 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3.5 + TypeScript + Vue Router + Vite 7 |
| 后端 | FastAPI 0.163 + SQLAlchemy 2.1 + Shapely 2.2 |
| 数据库 | PostgreSQL 18（JSONB 存原始载荷 / 门槛快照 / 证书结论） |
| 测试 | pytest（26 个用例，覆盖 7 个验收场景 + 修订/回溯 + 湿地修复比较 11 个用例） |

## 2. 一键启动（Docker Compose）

```bash
cd <仓库根目录>
docker compose up --build
```

启动后：

- 前端：<http://localhost:8080>
- 后端 OpenAPI 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/health>

首次启动时后端容器会等待 PostgreSQL 就绪，自动建表并写入演示种子数据
（2 个调查运行、16 个站点、48 个调查单元，覆盖主服务 7 个验收场景与湿地修复比较工作区）。

停止：`docker compose down`；连同数据卷清理：`docker compose down -v`。

## 3. 本地开发（不使用 Docker）

运行时已在容器内预装（Python 3.12 / Node 22 / PostgreSQL 18），也可用本机同版本。

### 3.1 数据库

```bash
# 数据库账号 ccdev / ccdev，端口 5432
createdb -h 127.0.0.1 -U ccdev survey_integrity        # 开发库
createdb -h 127.0.0.1 -U ccdev survey_integrity_test   # 测试库（pytest 使用）
```

### 3.2 后端

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export DATABASE_URL='postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/survey_integrity'
.venv/bin/python -m app.seed                # 建表 + 写入演示数据（可重复执行，已有数据则跳过）
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- API 根路径：`http://127.0.0.1:8000/api`
- Swagger UI：`http://127.0.0.1:8000/docs`
- OpenAPI JSON：`http://127.0.0.1:8000/openapi.json`

> 本项目为全栈应用（含业务前端页面），按任务约束同时提供可运行的 OpenAPI 3.x 文档与 Swagger UI，
> 便于接口对接与验收。

### 3.3 前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 ，/api 代理到 http://127.0.0.1:8000
```

生产构建：`npm run build`（产物在 `frontend/dist`，可用任意静态服务器托管，`/api` 反代到后端）。

### 3.4 测试

```bash
cd backend
DATABASE_URL='postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/survey_integrity_test' \
  .venv/bin/python -m pytest -q
```

测试会重建测试库的 `public` schema 并写入种子数据，**不会影响开发库**。

## 4. 测试账号

| 账号 | 密码 | 角色 | 权限 |
| --- | --- | --- | --- |
| `yq.li` | `Survey#2026` | 研究人员 researcher | 组装运行、登记/撤回趋势引用、修订记录与判读、处置冲突、重新签发证书 |
| `m.cao` | `Audit#2026` | 审核员 auditor | 同上（用于复核类操作与审计） |
| `l.lin` | `View#2026` | 只读访客 viewer | 只读查看所有数据 |

## 5. 业务模型与判定口径

### 5.1 调查单元

覆盖组装器按 `站点 × 样区 × 调查日`（规则册 `schedule.survey_dates`）生成调查单元。
每个单元固定引用：站点版本、规则册版本、概率模型版本，并把四类门槛的判定与证据记录 id 一并留存。

### 5.2 四类门槛

| 门槛 | 判定要点 | 典型结论 |
| --- | --- | --- |
| `device` 设备可用性 | 心跳间隔、连续图像帧序列（`frame_seq` 跳号）、停机日志覆盖；未解释缺口 → **待核** | 帧缺失且停机日志未覆盖 → 设备待核 |
| `time` 时间可信度 | 设备时间与可信源偏差 vs 容差（120s）；**存在可验证校时关系**才可回算；迟到送达只记录不改事件时间 | 偏移超容差且无校时 → 未满足 |
| `space` 空间覆盖 | 巡护轨迹对规则册路线段的覆盖、进入缓冲区深度、**定位精度半径 vs 缓冲区宽度**；按事件时间的站点版本取边界 | 仅擦过缓冲边缘且精度 > 缓冲宽度 → 空间待核 |
| `reading` 素材判读 | 达置信门槛的判读数、待判读占比、互斥判读冲突 | 互斥且均低于门槛 → 保留冲突、判读待核 |

### 5.3 证书状态与用途

| 状态 | 含义 | 正式未发现结论 | 趋势输入 |
| --- | --- | --- | --- |
| 有效调查 `valid` | 四类门槛均通过且无未决冲突 | ✅ | ✅ |
| 受限调查 `restricted` | 主要门槛通过但存在受限项 | ❌ | ✅ |
| 未有效调查 `invalid` | 存在未满足门槛 | ❌ | ❌ |
| 待核 `pending` | 存在待核实证据 | ❌ | ❌ |
| 复审中 `under_review` | 判读修订改变出现判定后签发的复审版本 | ❌ | ❌（暂停新引用） |

证书快照（门槛判定 + 结论）在签发时深拷贝保存；后续重算不会改写已签发证书。
复审会**新增一个证书版本**并把旧证书标记为被替代，旧运行仍可看到原证书原貌。

### 5.4 规则证书 vs 概率估计

- **规则证书**：逐项门槛，可向监管人员解释；接近门槛的部分路线会被排除，样本更少。
- **概率估计**：`DET-LOGIT` 检测概率模型（logistic，特征为设备可用比例、路线覆盖、达门槛判读数、待判读比例、
  缓冲区内素材比例、精度是否可信），时间可信与无冲突是**门控特征**（不满足则覆盖值为 0）。
  结果带 80% 敏感区间、校准集切换（pivot 偏移）与阈值上浮敏感性。
- 概率结果**仅用于趋势研究**，界面与接口均标注「模型推算，不是已确认的实地覆盖」，
  接口 `purpose=trend_only`，不会写入证书用途。

### 5.5 七个验收场景（种子数据）

| 站点 | 场景 | 期望结果 |
| --- | --- | --- |
| `QSL-01` 青松岭主监测站 | 完整调查 + 可验证校时 + 迟到送达 | 有效调查，可用于正式未发现结论 |
| `QSL-02` 青松岭北梁相机站 | 有心跳但帧连续缺失，停机日志未覆盖 | 设备可用性**待核**，不判未出现 |
| `QSL-03` 青松岭西沟站 | 两来源互斥判读且均低于 0.70 | 判读**待核**，保留冲突 |
| `QSL-04` 青松岭东南站 | 待判读占比过高（薄判读） | 判读待核；概率估计偏低 |
| `YXG-01` 云溪沟西站 | 轨迹只擦过缓冲边缘，精度 40m > 缓冲 30m | 空间**待核** |
| `YXG-02` 云溪沟东站 | 迟到判读修订：未出现 → 出现 | 当前证书**复审中**，旧引用暂停，旧证书保留 |
| `YXG-03` 云溪沟下坝站 | 路线覆盖 60%（接近门槛 50%/目标 80%） | 受限调查；概率估计仍过阈值 → 样本取舍分歧 |
| `BHP-01` 北坡高寒相机站 | 设备时钟偏移 900s 且无可验证校时 | 时间**未满足** → 未有效调查 |
| `BHP-02` 北坡垭口迁移站 | 4/11 迁移边界与路线段变更 | 4/11 用 v1、4/12 用 v2，各自判定 |

## 6. 验证方式（均已实际执行）

### 6.1 自动化测试

```bash
cd backend
DATABASE_URL='postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/survey_integrity_test' \
  .venv/bin/python -m pytest -q
# 26 passed
```
覆盖：

- `tests/test_scenarios.py`：7 个验收场景 + 校时放行 + 引用用途限制 + 回溯链完整性 + 无派工动作断言
- `tests/test_revisions.py`：判读修订触发复审与新证书版本、档案记录修订保留哈希链、
  单元证据全链可回溯、重算运行不改变旧证书
- `tests/test_comparison.py`：湿地修复调查比较工作区 11 个用例（见 6.5）

### 6.2 前端构建与类型检查

```bash
cd frontend
npx vue-tsc --noEmit     # 0 error
npm run build            # 构建成功
```

### 6.3 端到端界面验证（headless Chromium + Playwright）

已实际执行并逐页截图核对：

1. 登录 `yq.li / Survey#2026` → 站点覆盖总览：27 个矩阵单元、10 有效 / 3 受限 / 3 未有效 / 11 待核，
   门槛分布与「需关注的调查单元」正确联动。
2. 进入 `QSL-02` 单元：设备门槛显示「待核」，并列出未覆盖的帧缺失区间与小时数。
3. 进入 `YXG-01`：空间门槛「待核」，说明「精度 40m > 缓冲宽度 30m」。
4. 进入 `QSL-03`：判读「待核 · 1 项冲突未决」；冲突页展示两来源互斥结论并可记录处置。
5. 进入 `YXG-02`：证书「复审中」，趋势引用「已暂停」，证书历史含 v1（原签发）与 v2（复审）。
6. 进入 `BHP-01`：时间门槛「未满足」，说明无可验证校时关系。
7. `规则证书 vs 概率估计`：17 个可比较单元，10 一致支持、7 「概率支持 · 规则排除」；
   阈值敏感性表显示阈值上浮至 0.90 时可用样本降为 0。
8. `单元回溯链`：原始事件 → 覆盖条件 → 版本 → 证书 → 趋势引用五段齐全，记录含 64 位哈希。
9. `湿地修复调查比较`：生成新报告 → 处理区 6 个合格单元、5 个参照进入组合、3/3 个修复后比较日可报告；
   权重页显示每个候选的权重或排除理由；区域页显示林边参照区 3 个 2025 春季缺失调查日；
   敏感性页显示修复前拟合误差、留出日期与 5 项去一参照检验。
10. 登记 3 个参照区同期施工并重算 → 2026-04-11 / 04-12 两日切为「暂停」，
    报告状态「待复核」，暂停理由列出保留权重 17% < 60%，并可切回旧报告看到原值 +3.042 / +1.614。
11. 在工程资料页把实际开工日期更正为 2026-03-01 并重算 → 变化来源显示 `engineering_input_revision`，
    权重变化逐项列出，方案签名未变。
12. 浏览器控制台无报错。

### 6.4 API 冒烟

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"yq.li","password":"Survey#2026"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s http://127.0.0.1:8000/api/runs -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/runs/1/overview -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/units/1/trace -H "Authorization: Bearer $TOKEN"

# 湿地修复比较工作区
PLAN=$(curl -s http://127.0.0.1:8000/api/comparison/plans -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["id"])')
curl -s -X POST http://127.0.0.1:8000/api/comparison/plans/$PLAN/analyse \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"method":"weighted_multi_reference","note":"README 冒烟"}'
```

### 6.5 湿地修复比较工作区验收（`tests/test_comparison.py`，已实际执行）

```bash
cd backend
DATABASE_URL='postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/survey_integrity_test' \
  .venv/bin/python -m pytest -q tests/test_comparison.py
# 11 passed
```

覆盖内容：

- **真实结果**：1 个修复区 + 6 个候选参照，输出 3 个修复前共同日期与 3 个修复后比较日的
  处理区/参照组合观测率与差异；每个候选显示贡献权重或排除理由。
- **权重只用修复前**：把观察窗口截断到修复前，重算的权重与完整窗口一致（差异 <2%），
  证明看到修复后结果不会改变权重。
- **参照受施工影响**：3 个高权重参照在观察季开工后，受影响日期权重保留 <60%，
  相关结论转为待复核，暂停项逐条列出上一份报告与受影响调查单元。
- **部分季节缺失**：林边参照区 2025 春季无调查单元，缺失日期明确列出且**不计为 0 次观测**，
  该候选以“修复前证据不足”排除。
- **描述性降级**：把修复前共同窗口门槛抬高后，不拟合权重、不产出差异，只报告描述性差异并说明原因。
- **工程日期回溯更正**：更正实际开工日期后重算，变化来源标注为 `engineering_input_revision`
  （与方案变化分开），可比单元与权重变化逐项列出。
- **有效努力量校准**：补录调查可用性说明后，有效努力量下降而原始检出数不变。
- **无审批/评优**：OpenAPI 路径与比较接口响应中不出现 approve/score/ranking 等字样。
- **补录停机日志与扩大影响边界**：通过主服务补录停机日志并重建运行后重算，变化归因为调查输入修订；
  再登记覆盖参照站的扩大施工阶段，WR-R4 的受影响单元被逐日剔除并可追到具体单元。
- **报告冻结**：重算新增报告并链到上一份，旧报告的存储结果不被读取或重算改写。

### 6.6 湿地修复比较的业务口径

| 主题 | 处理方式 |
| --- | --- |
| 观测率 | 每个调查单元先算有效努力量（可用相机小时/24 × 判读完成比例，可再按可用性说明折算），再用「检出次数 ÷ 有效相机日」比较；照片总数只作核对，不代入观测率 |
| 证书适用性 | 只读取主服务的当前证书与门槛；复审中、无证书或不合格的单元从比较剔除，区域自身观测事实仍可查看 |
| 参照权重 | 只用修复前共同合格日期拟合：生境相似度 × 修复前覆盖率 × 1/(1+修复前拟合误差)，归一化后进入组合；修复后结果不参与权重 |
| 施工影响 | 按工程资料（阶段）的实际边界与实际日期判定；落在影响范围内的单元逐日剔除，不因行政归属不同认定未受影响 |
| 参照区季中开工 | 仅保留开工前符合窗口的部分；修复后权重保留不足 60% 的日期暂停报告，剩余证据不足时整份报告转为「待复核」 |
| 缺失调查 | 期望调查日无合格单元时列为缺失，**不计为 0 次观测** |
| 无共同修复前窗口 | 降级为描述性差异，不强行拟合 |
| 变化归因 | 报告记录输入批次（调查指纹 + 工程输入指纹 + 方案签名）；重算时分开说明「输入修订」与「方案变化」，差异逐项可追到单元 |
| 报告冻结 | 每次重算新增报告并链到上一份，旧报告保留当时证据与结论，不被改写 |

## 7. 界面

| 页面 | 说明 |
| --- | --- |
| 站点覆盖总览 | 站点 × 调查日矩阵、门槛分布、需关注单元、最近数据活动 |
| 单元详情 | 规则证书 vs 概率估计并列；四门槛证据；原始证据表；证书历史；审计；引用登记 |
| 规则证书 vs 概率估计 | 逐单元并列对照、分歧分类、阈值敏感性、单单元敏感度复算 |
| 趋势输入清单 | 按用途/状态筛选引用，显示引用时证书快照与概率区间 |
| 单元回溯链 | 原始事件 → 覆盖条件 → 规则/模型/站点版本 → 证书 → 趋势引用 |
| 观测档案 | 类型/站点/编号检索，原始载荷、哈希、修订历史、载荷修订 |
| 判读冲突 | 未决冲突的来源、置信度、处置记录 |
| 样区规则册与模型 | 规则册四类门槛、路线段、海拔带；模型校准集、权重、阈值复核 |
| 湿地修复调查比较 | 处理区/参照区观测比较、候选权重与排除理由、缺失调查日、施工影响剔除、敏感性、工程资料维护、输入批次与报告历史 |

## 8. 主要 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/login` | 登录，返回 HMAC 签名令牌 |
| GET | `/api/runs` | 运行列表（含状态计数、未决冲突数） |
| POST | `/api/runs` | 按规则册/模型/站点组装新运行 |
| GET | `/api/runs/{id}/overview` | 总览：矩阵、门槛分布、对照行、活动 |
| GET | `/api/runs/{id}/units` | 单元列表（可按站点/证书状态/门槛过滤） |
| GET | `/api/units/{id}` | 单元详情：门槛、证书、概率、证据、冲突、审计 |
| GET | `/api/units/{id}/trace` | 可回查的五段回溯链 |
| GET | `/api/units/{id}/records` | 单元引用的原始记录（含修订） |
| POST | `/api/units/{id}/trend-references` | 登记 `absence` / `trend` 引用（含用途校验） |
| POST | `/api/units/{id}/certificates/reissue` | 复审后重新签发证书 |
| GET/POST | `/api/records`、`/api/records/{id}/revisions` | 档案检索、载荷修订（哈希 + 修订号） |
| POST | `/api/determinations`、`/api/determinations/{id}/revisions` | 判读新增/修订（触发复审） |
| GET/POST | `/api/conflicts`、`/api/conflicts/{id}/resolve` | 冲突列表与处置 |
| GET/POST | `/api/trend-references`、`/api/trend-references/{id}/revoke` | 引用列表与撤回 |
| POST | `/api/probability/models/{id}/evaluate` | 概率复算（可选校准集偏移，只读不签署） |
| GET | `/api/probability/models/{id}/threshold-sensitivity` | 阈值敏感性 |
| GET | `/api/rulebooks`、`/api/models`、`/api/stations`、`/api/zones`、`/api/audit` | 规则册、模型、站点、样区、审计 |
| GET | `/api/comparison/plans`、`/api/comparison/plans/{id}` | 比较方案（成员、观察窗口、纳入依据、锁定状态、历史报告） |
| GET | `/api/comparison/areas`、`/api/comparison/areas/{id}/observation` | 比较区域与逐调查单元观测（含有效努力量、排除原因） |
| POST | `/api/comparison/plans/{id}/analyse` | 生成比较报告（多参照加权 / 单参照 / 描述性） |
| POST | `/api/comparison/plans/{id}/recompute` | 按当前输入重算并给出“输入修订 vs 方案变化”说明 |
| GET | `/api/comparison/plans/{id}/analyses`、`/api/comparison/analyses/{id}` | 报告历史与冻结报告详情 |
| GET/POST | `/api/comparison/projects`、`/api/comparison/projects/{id}`、`/api/comparison/projects/{id}/revisions` | 工程资料（空间范围、阶段、实际日期、来源）与日期/边界修订 |
| POST | `/api/comparison/projects/{id}/phases` | 登记施工阶段；可登记为同期独立工程（参照区自身开工） |
| POST | `/api/comparison/availability-notes` | 补录调查可用性说明（按比例折算有效努力量，不改原始记录） |

## 9. 目录结构

```
backend/
  app/
    main.py            # FastAPI 应用与路由装配
    models.py          # 18 张表（档案、修订、判读、规则册、单元、门槛、证书、冲突、模型、引用、审计）
    coverage.py        # 覆盖组装器：四类门槛判定与证据留存
    certificates.py    # 有效性裁定：状态、用途、复审版本
    probability.py     # 检测概率模型（含门控特征与敏感区间）
    restoration.py     # 湿地修复比较：适用性读取、有效努力量校准、修复前权重、敏感性与变更归因
    services.py        # 组装运行、记录/判读修订、引用、冲突处置、重签发
    seed.py            # 确定性种子数据（7 个验收场景）+ 调用 seed_wetland
    seed_wetland.py    # 湿地修复比较种子（2 个季节、7 个比较区、1 个工程资料与阶段）
    routers/           # auth / catalog / surveys / records / probability / comparison
  tests/               # 26 个 pytest 用例
frontend/
  src/views/           # 9 个业务页面
  src/components/      # 状态徽章、概率区间条、提示
docker-compose.yml     # db + backend + frontend 一键启动
```

## 10. 数据与合规说明

- 数据库中的 `ccdev` 账号仅用于隔离开发/测试，不作为生产配置。
- 所有时间以保护区当地时区（UTC+8）解释、UTC 存储；时间校准关系仅在有 `verified: true` 且落在
  有效区间内时才用于回算事件时间。
- 服务端不包含任何派工、预约、执法、捕捉或资源调度接口；测试 `test_service_emits_no_dispatch_actions`
  会校验 OpenAPI 路径与总览响应中不出现相关动作。
