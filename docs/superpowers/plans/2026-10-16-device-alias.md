# 方案 50：设备别名/备注 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 已连接设备可添加备注（别名），Dashboard 表别名优先展示 + 行内编辑 + 备注/型号/设备ID 三字段搜索；别名持久化于 user.yaml `devices.<id>` 节，跨连接保留。

**Architecture:** 复用方案 45 设备级覆盖整链——`DeviceOverride` 增 `alias` 字段、`PUT /api/settings` 的 `_merge_devices` 扩展 alias 合并语义；读取侧 `DeviceInfo` 增瞬态 `alias`（仿 `slow` 先例），`DeviceService.list_devices/get_device` 以 `config.settings.device_alias()` 单源 helper 填充，SSE connected 事件带出。前端 Dashboard 表格本地过滤 + `ElMessageBox.prompt` 编辑；Settings 设备下拉标题带别名（顺带修复其 `device_id` 字段形状潜在缺陷）。

**Tech Stack:** FastAPI + pydantic v2 + PyYAML（后端）；Vue 3 + Element Plus + Pinia + vitest（前端）。

**Spec:** `方案/50-设备别名备注与FreeControl2增量对比调研.md`（D1=A / D2=留空回落序列号 / D3=三字段搜索 / D4=本期仅 Dashboard+设置页下拉，均已拍板）

## Global Constraints

- 零新依赖；master 直做；提交中文、无 Co-Authored-By；不 push（待显式授权）。
- 后端门禁：ruff `backend/app` + mypy strict + pytest cov≥80（PYTHONPATH=backend）。
- 前端门禁：vitest 全绿 + vue-tsc + `npx eslint .` 全量。
- 别名规则：≤64 字符（含 trim 后）；空串=清除；留空展示回落序列号（不自动「设备N」）；不校验设备在线（写入口复用 /api/settings）。
- SQLite devices 表不动（alias 不入库，瞬态）。
- 测试文件放 tests/（后端）与 src 同目录 .test.ts（前端惯例）。

## Review Focus

- user.yaml 已有 devices 节（仅 stream/recording 旧数据）→ 加 alias 后读写合并不得洗掉旧键；`_merge_devices` 对非映射 dev_cfg 响亮报错语义保持。
- alias trim 后为空串 → 清除该键（而非写入空字符串），GET 视图与设备列表同步消失。
- alias 含中文/空格/emoji：DeviceId 键白名单不受影响；alias 值仅限长不lim字符。
- SSE connected payload 手工构字段处——alias 缺失会让前端本地增量更新丢备注（重拉列表才回来）。
- 搜索框过滤后的批量断开选择态：el-table selection 与过滤行交互（过滤时选中行消失 → selection-change 收敛，不残留幽灵选中）。

---

### Task T1：后端 alias 读写链

**Files:**
- Modify: `config/settings.py`（DeviceOverride 398-404 区块 + 新 helper）
- Modify: `backend/app/interfaces/http/settings.py`（_merge_devices 48-80）
- Modify: `backend/app/domain/device.py`（DeviceInfo）
- Modify: `backend/app/application/device_service.py`（list_devices 111-137、get_device 193-170 附近）
- Modify: `backend/app/interfaces/http/devices.py`（SSE on_connected 94-106）
- Test: `tests/unit/test_settings_api.py`、`tests/application/test_device_service.py`、（如存在 SSE 断言面）`tests/unit/test_devices_api.py`

**Interfaces:**
- Produces: `DeviceOverride.alias: str | None`；`config.settings.device_alias(device_id) -> str`（无则 ""）；`DeviceInfo.alias: str = ""`；PUT devices 补丁 `{ "<id>": { "alias": "<文本>" } }`，`"alias": ""` = 清除。
- Consumes: 既有 `settings_store` 原子写、`GET /api/settings` devices model_dump（alias 自动带出，`exclude_none=True`）。

- [ ] **Step 1（红）**：`tests/unit/test_settings_api.py` 新增：
  1. `test_put_device_alias_roundtrip_and_get_view`：PUT `devices:{d1:{alias:"主力机"}}` → GET payload `devices.d1.alias == "主力机"`，且同设备已有 stream 覆盖不被洗掉（先 PUT stream 覆盖再 PUT alias，两键并存）。
  2. `test_put_device_alias_trims`：`"  A  "` → `"A"`。
  3. `test_put_device_alias_empty_string_clears`：先设后清（`alias:""`）→ GET 视图无 alias 键、stream 仍在。
  4. `test_put_device_alias_rejects_too_long`：65 字符 → 422（pydantic max_length 经 SettingsPatch 校验）。
  `tests/application/test_device_service.py` 新增：
  5. `TestListDevices::test_list_devices_attaches_alias`：monkeypatch `config.settings.device_alias` 返回映射值 → 列表条目 alias 填充；未配置为 ""。且 `repo.save` 收到的对象入库列不含 alias（save 走既有 9 列——断言 repo mock 即可）。
  6. `TestGetDevice::test_get_device_attaches_alias`。

- [ ] **Step 2**：跑新用例确认失败（`PYTHONPATH=backend python -m pytest tests/unit/test_settings_api.py tests/application/test_device_service.py -x -q`）。

- [ ] **Step 3（绿）**：实现——
  - `config/settings.py`：`DeviceOverride` 增 `alias: str | None = Field(default=None, max_length=64)`（注释：设备备注，user.yaml 持久，读取侧经 device_alias 消费）。新增模块级 `def device_alias(device_id: str) -> str: dev = settings().devices.get(device_id); return (dev.alias if dev else None) or ""`。
  - `interfaces/http/settings.py::_merge_devices`：每设备 dict 合并段追加：`if override.alias is not None:` → strip 后非空写 `dev_cfg["alias"]`、为空 `dev_cfg.pop("alias", None)`。
  - `domain/device.py`：DeviceInfo 增 `alias: str = ""`，docstring 属性表补一行（瞬态，不入库，读取侧由 service 填充）。
  - `application/device_service.py`：list_devices 返回前与 get_device 返回前 `alias = device_alias(d.id)` 赋值（import from config.settings；application 层读配置已有先例）。
  - `interfaces/http/devices.py`：SSE `on_connected` payload dict 增 `"alias": device.alias or device_alias(device.id)`（走 helper 单源，不依赖回调对象是否已填充）。

- [ ] **Step 4**：目标文件复跑全绿 + `ruff backend/app` + `mypy backend/app --strict`（或项目现行 mypy 命令）零红。

- [ ] **Step 5**：提交 `feat: 方案 50 T1 后端设备别名读写链——DeviceOverride.alias（≤64/trim/空串清除）+ device_alias helper + DeviceInfo 瞬态字段列表/详情/SSE 带出（user.yaml 持久，SQLite 不动）`。

### Task T2：前端展示/编辑/搜索

**Files:**
- Modify: `frontend/src/services/api.ts`（DeviceOverrideView 118-121、SettingsPatch.devices 135）
- Modify: `frontend/src/stores/device.ts`（DeviceInfo 39-53；新 action `setLocalAlias`）
- Modify: `frontend/src/views/Dashboard.vue`（表格列 179、操作列 247-263、`:data` 172、脚本区）
- Modify: `frontend/src/views/Settings.vue`（deviceList 238/420-423、el-option 150）
- Test: `frontend/src/views/Dashboard.test.ts`、`frontend/src/views/Settings.test.ts`、`frontend/src/stores/device.test.ts`（按需）

**Interfaces:**
- Consumes: T1 的 GET /api/devices `alias` 字段、GET /api/settings devices alias、PUT `devices:{[id]:{alias}}`。
- Produces: `setLocalAlias(id: string, alias: string)`（本地即时更新，alias 空串 → 置 ''）。

- [ ] **Step 1（红）** Dashboard.test.ts 新增：
  1. 别名优先渲染：devices=[{id:'d1',alias:'主力机',...}] → 首列含「主力机」与「d1」两行文本；无 alias 行仅 id。
  2. 搜索过滤：输入「主力」→ 仅匹配行显示（model/id 匹配各一例，大小写不敏感一例）；清空恢复。
  3. 备注编辑：点「备注」→ prompt 确认「新机」→ 断言 `api.putSettings` 收到 `{devices:{'d1':{alias:'新机'}}}` 且该行显示更新；输入空串 → 提交 `{alias:''}` 且显示回落 id；取消 prompt → 不写。
  Settings.test.ts：mock listDevices 形状改 `[{ id: 'd1', alias: '主力机' }]` → 下拉项 label 含「主力机（d1）」、value=d1；`?device=d1` 聚焦成立（钉死形状缺陷）。

- [ ] **Step 2**：`cd frontend && npx vitest run src/views/Dashboard.test.ts src/views/Settings.test.ts` 红。

- [ ] **Step 3（绿）**：实现——
  - api.ts：`DeviceOverrideView` 增 `alias?: string`；`SettingsPatch.devices` 值类型放宽为 `{ stream?: Record<string, unknown>; recording?: Record<string, unknown>; alias?: string } | null`（现结构兼容）。
  - stores/device.ts：`DeviceInfo` 增 `alias?: string`（注释：后端瞬态，列表带出）；action `setLocalAlias(id, alias)`。
  - Dashboard.vue：
    - 工具条（batch-bar 上方，h2 行下方）`el-input v-model="search" placeholder="搜索备注/型号/设备ID" clearable` + prefix 搜索图标。
    - `filteredDevices` computed：三字段 `toLowerCase().includes(kw)`，空 kw 直通；`:data="filteredDevices"`。
    - 设备ID 列改 template：`<div class="device-id-cell"><span class="alias">{{ row.alias || row.id }}</span><small v-if="row.alias">{{ row.id }}</small></div>`（有别名双行、无别名单行）。
    - 操作列宽 200→260，加「备注」按钮：`ElMessageBox.prompt`（title=设置设备备注，inputValue=当前 alias，inputValidator ≤64，confirm/cancel 文案）→ trim 后 `api.putSettings({ devices: { [row.id]: { alias } } })` → `store.setLocalAlias(row.id, alias)` → ElMessage.success('备注已更新')/（空串时 '备注已清除'）；cancel（reject）吞掉。
  - Settings.vue：deviceList 类型改 `{ id: string; alias?: string }`，L420 去掉错误 cast、`?device=` 匹配与 el-option 全部改 `d.id`，label `d.alias ? \`${d.alias}（${d.id}）\` : d.id`。

- [ ] **Step 4**：vitest 相关全绿 → `npx vue-tsc --noEmit` → `npx eslint .` 全量零红。

- [ ] **Step 5**：提交 `feat: 方案 50 T2 前端设备别名——Dashboard 备注优先展示/行内编辑（PUT user.yaml devices 节）/三字段搜索过滤；设置页设备下拉标题带别名并修正 id 字段形状`。

### Task T3：文档回写与收口

- [ ] README.md 功能/更新段补一行设备备注；docs/用户手册.md 设备管理节补「备注」与搜索说明（≤64 字、留空回落、断线保留）。
- [ ] 全量门禁：pytest（cov≥80）+ vitest + vue-tsc + eslint 全量 + ruff/mypy。
- [ ] 进度追踪第九十五次实施记录、方案 50 状态行→已实施、路线图 19 行→✅、roadmap 更新记录。
- [ ] 关键验收节点评审一次（评审节奏口径）；提交不 push。
- [ ] 真机/浏览器人工验收清单交用户：连接设备→加备注→刷新仍在→断连重连备注仍在→搜索命中→清除备注回落序列号→设置页下拉带别名。
