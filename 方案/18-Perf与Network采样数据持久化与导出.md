# 18 - Perf / Network 采样数据持久化与导出

## 目标

Perf（CPU/内存/FPS/Activity）与 Network（流量速率/连接数/WiFi）采样数据支持导出为文件，
同时保证磁盘占用可控：

1. **暂存即用即清**：采样期间的暂存数据在采集结束后立即释放，默认不占磁盘；
2. **导出可保留**：导出动作产出可长期保存的文件（CSV/JSON 下载）；
3. **缓存定时清理**：凡是落盘的数据都是"缓存"，定时按时间 + 容量双限清理，保证硬盘容量。

## 状态

- ⏳ 待实施（本文为调研 + 设计，2026-09-22 输出）

## 依赖

- [08-性能监控.md](./08-性能监控.md) ✅（Perf 采集/推送/前端展示已落地）
- [14-调试面板其他标签完善.md](./14-调试面板其他标签完善.md) ✅（Perf/Network 标签已落地）
- 既有可复用件：`DatabasePool`（WAL 连接池）、`BatchLogWriter`（批写范式）、
  `DebugService` 清理循环与仓储方法（`delete_old_*` / `trim_*` / `vacuum`）、
  日志导出端点（`StreamingResponse` 范式）、`config/settings.py` 分节配置与热重载

## 一、调研（2026-09-22）

同类项目对"采样数据暂存 / 保留 / 清理"的处理先例：

| 项目 | 暂存（采样中） | 保留（持久化） | 清理策略 |
|------|----------------|----------------|----------|
| Perfetto（Android 系统 trace） | 内存 ring buffer（默认 32MB；`fill_policy`：RING_BUFFER 覆盖最旧 / STOP_WHEN_FULL 写满即停） | 仅显式 "Write into file" 才落盘成 trace 文件 | 文件由用户/宿主管理，工具自身不留 |
| Android Studio Profiler | 会话数据在 IDE 内存中（实时经 adb 采集），无自动落盘 | 显式导出 `.trace` / `.hprof` / `.har` 才持久 | 进程断开即会话失效；未导出数据丢失（设计如此） |
| Prometheus（TSDB） | WAL + 2h block | 本地 TSDB 长期保留 | `retention.time`（默认 15d）+ `retention.size`（超限删最旧 block），**两条件 OR，谁先到算谁**；删除不立即释放空间 |
| Netdata（dbengine） | tier0 秒级先入内存 ring buffer 再落盘 | 分级保留：秒级 14d / 分钟级 60d / 小时级 365d | 每档「1GiB 或时间上限，谁先到算谁」（hybrid 语义） |
| OTel file exporter（lumberjack） | 无（直写） | 文件即产物 | 轮转参数 `max_megabytes` / `max_days` / `max_backups` 三限 |
| SQLite 通用实践 | — | — | DELETE 不缩文件（页进 freelist 复用）；`VACUUM` 重建回收（需约 2x 空间、锁库）；`auto_vacuum` 须建表前设置 |

**三条设计结论**：

1. **暂存与保留分离**（Perfetto / Profiler 范式）：默认零落盘，只有显式动作（导出 / 录制）才产生持久产物；
2. **清理必须双限**（Prometheus / Netdata 范式）：时间保留期 + 容量上限，「谁先到算谁」，超限删最旧；
3. **空间回收必须显式**（SQLite 事实）：DELETE 只回收逻辑空间，需低频 `VACUUM` 才真正缩文件——本项目 logcat 清理已走通该路径，直接复用。

## 二、现状（代码事实）

| 侧 | 现状 | 缺口 |
|----|------|------|
| Perf 后端 | `PerformanceService`：每设备内存 `deque(maxlen=3600)` + 1s 采样循环 + WS 订阅分发（`/ws/perf/{device_id}`）；`GET /api/perf/{device_id}/metrics?limit=` 仅读内存；`stop_monitoring` 直接 `_buffers.pop()` 丢弃缓冲 | 无导出；停止即全丢（无"保留"通路）；无落盘 |
| Network 后端 | 无服务层：`interfaces/http/network.py` 内按设备缓存 `NetworkSampler`（TTL 300s，为算速率保留上一次采样）；`/stats`、`/connections` 均为即时快照 | 无历史缓冲、无服务、无导出、无落盘 |
| 前端 | PerfView 经 WS 收数、仅保留最近 60 点渲染；NetworkView 每 2s HTTP 轮询、仅保留最近 60 点；二者均无导出入口 | 导出按钮；可选录制开关 |
| 清理 | `DebugService._cleanup_loop` → `run_cleanup()`：删过期日志 + 删过期 shell 历史 + 超库容量删最旧日志 + 返回库大小；仓储层有 `delete_old_logs` / `trim_logs_to_db_size` / `get_db_size_bytes` / `vacuum()` | 清理不覆盖指标数据 |
| 存储 | `data/debug.sqlite`（DatabasePool + WAL），仅 `debug_sessions` / `debug_logs` / `shell_history` 表 | 无指标表 |

## 三、设计

### 3.1 数据生命周期（三态）

| 态 | 载体 | 产生条件 | 生命周期 | 清理者 |
|----|------|----------|----------|--------|
| **暂存态** | 内存 ring buffer（每设备） | 监控运行中（WS 活跃 / 显式 start） | 停止监控 / 设备离线 / 服务关闭 → **立即释放** | 服务自身（finally / stop 路径） |
| **缓存态** | SQLite `perf_samples` / `network_samples` | 仅"录制"开启时落盘（显式动作） | 落盘后保留，供导出与回溯 | 定时清理任务（时间 + 容量双限，低频 VACUUM） |
| **交付态** | 浏览器下载文件 | 用户点"导出" | 用户自行保管 | 服务端零占用（流式下载，不留临时文件） |

对应三条目标：暂存态满足"执行完成即清除、不占空间"；缓存态 + 交付态满足"导出时能保留数据"；
定时清理覆盖缓存态，满足"保证硬盘容量"。

### 3.2 关键决策

- **D1 默认不落盘**：`metrics.recording` 默认 `false`。不录制时磁盘占用恒为 0，内存环缓冲有硬上限，
  停止监控即释放——直击"不占空间"诉求（Perfetto/Profiler 范式）。
- **D2 导出两个来源**：`source=buffer`（默认，内存暂存快照，**零磁盘，任何时刻可用**）与
  `source=cache`（缓存态区间，需曾录制；支持 `from`/`to` 过滤）。导出为流式下载
  （`StreamingResponse` + `Content-Disposition`），复用 logcat 导出现范。
- **D3 双限清理**：`metrics.retention_days`（默认 **3 天**）+ `metrics.max_rows_total`
  （默认 **50 万行**，Perf+Network 合计），两条件 OR，超限**按 ts 删最旧**（分批 DELETE）。
  容量用**行数**而非字节：行宽固定可预测、不依赖 `dbstat`；库级字节兜底沿用
  `debug.max_db_size_mb`（见 D5）。
- **D4 删除后低频 VACUUM**：仅在本次清理确有删除时调用（复用仓储 `vacuum()`），
  避免每次空转重建库；VACUUM 锁库特性由"低频 + 清理周期（默认 1h）"吸收。
- **D5 共库与兜底顺序**：指标与日志同库 `data/debug.sqlite`。库级超限（`debug.max_db_size_mb`）
  的裁剪顺序扩展为 **日志 → shell 历史 → 指标**（各按 ts 删最旧），指标自身再受 D3 约束。
  理由：日志是主业务数据，指标是可再生采样。
- **D6 界面缩略与后端解耦**：前端 60 点仅用于绘图，导出走后端缓冲（最多 3600 点），
  前端不需要为导出增大内存。

**不做**（YAGNI）：分级降采样（Netdata tier 范式，数据量未到需要时）、独立 TSDB、
服务端导出文件保留目录（流式即弃，无需清理）、指标聚合报表。

### 3.3 API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/perf/{device_id}/export` | `?format=csv\|json&source=buffer\|cache&from=&to=&limit=` → 附件下载 |
| GET | `/api/network/{device_id}/export` | 同上（字段为网络指标） |
| POST | `/api/perf/{device_id}/record/start` | 开启录制（幂等；返回 `recording: true`） |
| POST | `/api/perf/{device_id}/record/stop` | 停止录制（幂等；返回落盘行数） |
| GET | `/api/perf/{device_id}/record/status` | `{recording, rows, oldest_ts, newest_ts}` |
| POST | `/api/network/{device_id}/record/start\|stop`、GET `.../record/status` | 对称 |

既有端点（`GET /api/perf/{device_id}/metrics`、`/api/network/{device_id}/stats|connections`）保持不变。

### 3.4 存储 Schema

```sql
CREATE TABLE IF NOT EXISTS perf_samples (
    device_id   TEXT NOT NULL,
    ts          REAL NOT NULL,
    cpu_percent REAL,
    total_memory_mb REAL,
    used_memory_mb  REAL,
    fps         REAL,
    jank_count  INTEGER,
    current_activity TEXT,
    top_package TEXT
);
CREATE INDEX IF NOT EXISTS idx_perf_samples_device_ts ON perf_samples(device_id, ts);

CREATE TABLE IF NOT EXISTS network_samples (
    device_id   TEXT NOT NULL,
    ts          REAL NOT NULL,
    rx_bytes    INTEGER,
    tx_bytes    INTEGER,
    rx_rate_kbps REAL,
    tx_rate_kbps REAL,
    active_connections INTEGER,
    wifi_connected INTEGER,
    wifi_ssid   TEXT
);
CREATE INDEX IF NOT EXISTS idx_network_samples_device_ts ON network_samples(device_id, ts);
```

写入走批量（100 条 / 100ms，复用 `BatchLogWriter` 范式，`executemany` 单事务）；
`NetworkSampler.get_connections` 的连接明细**不入库**（属于即时快照，非时间序列）。

### 3.5 配置新增

```yaml
metrics:
  buffer_size: 3600           # Perf 内存暂存条数（@1s 采样约 1 小时/设备）
  network_buffer_size: 1800   # Network 内存暂存条数（@2s 采样约 1 小时/设备）
  network_interval: 2.0       # Network 采样间隔（秒）
  recording: false            # 录制（落盘暂存）总开关，默认关闭
  retention_days: 3           # 缓存态保留期（天）
  max_rows_total: 500000      # 缓存态容量上限（行，Perf+Network 合计）
```

清理周期沿用 `debug.cleanup_interval_hours`（默认 1h），不新增节拍。
所有键进 `AppConfig`/新 `MetricsConfig` 并支持配置热重载（运行时读取，见方案 01）。

## 四、实施步骤

### Step 1：NetworkService（暂存层补齐）

`backend/app/application/network_service.py`（新建，应用层）：
- 每设备采样循环（`network_interval`，默认 2s）+ `deque(maxlen=network_buffer_size)` 环形缓冲；
- 订阅分发（供未来 WS 化，当前前端仍走 HTTP 轮询，可不暴露订阅）；
- `interfaces/http/network.py` 收敛：`_get_sampler` 的即时快照改为经 service 读取
  （缓冲最新点新鲜度 < interval 时直接复用，避免与采样循环重复执行 ADB 命令）；
- 停止/设备离线 → 释放缓冲（`finally` 路径，对齐 `PerformanceService`）。

### Step 2：内存生命周期治理（暂存态清零）

- Perf：`stop_monitoring` 已有 `_buffers.pop`；补"无订阅且空闲 TTL 到期自动停采"（复用
  `PerformanceSampler.sample()` 自然结束路径），确保无人看时暂存不驻留；
- Network：同规则（空闲 TTL 默认 300s，沿用 HTTP 层现有 `_SAMPLER_TTL` 语义上移）；
- 服务关闭：`lifecycle` 现有 `performance_service.cleanup()` 语义扩展到 network。

### Step 3：录制（缓存态落盘）

- Schema 建表（`infrastructure/persistence/sqlite.py` 初始化处幂等 `CREATE TABLE IF NOT EXISTS`）；
- 仓储方法（`domain/ports.py` Protocol + SQLite 实现）：
  `save_perf_samples_bulk` / `save_network_samples_bulk`、`delete_old_metrics(retention_seconds)`、
  `trim_metrics_rows(max_rows)`（超限按 ts 删最旧，分批）、`count_metrics_rows()`；
- 录制器：录制开关 per-device，采样循环在 `recording=true` 时同时写入批量缓冲；
  `stop` 时 final flush，返回落盘行数；
- `metrics.recording` 为总开关（默认关），per-device 开关为运行态。

### Step 4：导出端点

- `interfaces/http/performance.py` / `network.py` 各加 export 端点；
- `source=buffer`：序列化 ring buffer 快照；`source=cache`：按 `device_id + ts 区间` 查库；
- CSV 表头：Perf = `ts,cpu_percent,total_memory_mb,used_memory_mb,fps,jank_count,current_activity,top_package`；
  Network = `ts,rx_bytes,tx_bytes,rx_rate_kbps,tx_rate_kbps,active_connections,wifi_connected,wifi_ssid`；
- JSON 与 logcat 导出一致（对象数组）；
- 上限保护：`limit` 默认 50000，防一次性拉爆内存。

### Step 5：清理扩展

- `run_cleanup()` 追加：`delete_old_metrics(retention_days)` → `trim_metrics_rows(max_rows_total)`
  → 有删除才 `vacuum()`；
- 库级兜底：`trim_logs_to_db_size` 语义扩展为跨表 `trim_to_db_size`（顺序：日志 → shell 历史 → 指标）；
- 清理结果字典加 `metrics_deleted` / `metrics_trimmed` 字段，日志同步。

### Step 6：前端

- `PerfView.vue` / `NetworkView.vue` 工具栏加：导出按钮（`window.open('/api/perf/{id}/export?format=csv')`，
  与 LogcatView 一致）、格式选择（CSV/JSON）、录制开关 + 录制中徽标（沿用 LIVE 指示器样式）；
- `api.ts` 加 `exportPerfMetrics` / `exportNetworkStats` / `startRecording` / `stopRecording`
  （导出走 URL，无需 axios 下载逻辑）。

### Step 7：测试与文档

- 后端单测：缓冲上限与淘汰、停止释放、录制批量写入与 final flush、导出 CSV/JSON 内容与
  Content-Disposition、清理（时间删除 / 行数裁剪 / 无删除不 VACUUM）、source=cache 区间过滤；
- 前端 vitest：导出按钮触发的 URL、录制开关状态流转、录制徽标；
- 真机冒烟（.18/.33）：录制 10 分钟 → 导出核对行数与数值 → 停止后暂存释放（内存/库大小）→
  手工触发清理验证删除与 VACUUM；
- 文档回写：`docs/API.md`（新端点）、`docs/架构.md`（NetworkService、指标表）、
  本方案状态、`方案/进度追踪.md`。

## 五、测试策略

| 层级 | 覆盖点 |
|------|--------|
| 单元（pytest） | ring buffer 上限/淘汰、录制开关与批写、导出两来源与格式、清理双限与 VACUUM 条件、热重载读取新配置 |
| 前端（vitest） | 按钮/开关/徽标交互，URL 拼装 |
| 真机（手工，.18/.33） | 采样精度对照（与 `dumpsys`/`/proc/net/dev` 手工取值核对）、录制-导出-清理全链路、磁盘占用前后对比 |

## 六、验收标准

- [ ] 不录制时，采样过程零磁盘写入；停止监控/设备离线后内存暂存释放（可测：停止后缓冲为空）
- [ ] 导出在 buffer 与 cache 两来源下均产出正确 CSV/JSON，且服务端无临时文件残留
- [ ] 缓存态数据超 `retention_days` 或 `max_rows_total` 时被清理（先删最旧），且删除后库文件体积下降（VACUUM 生效）
- [ ] `metrics.recording` 关闭时录制端点报错或拒绝（行为明确，不静默）
- [ ] 既有 Perf/Network 实时展示与 `/metrics`、`/stats` 行为不回归
- [ ] 门禁：pytest（含覆盖率 ≥80%）、ruff、mypy strict、前端 vitest 全绿

## 七、风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| ADB 采样开销叠加（top/dumpsys 每条命令进程开销） | 中 | Network 默认 2s 且复用缓冲新鲜点；Perf 维持现有 1s；多设备时按需降频（配置项已预留） |
| SQLite 写放大（录制时的持续写入） | 中 | 批量写（100 条/100ms 单事务）；默认关闭录制 |
| VACUUM 锁库 | 低-中 | 仅在有删除时执行；清理周期 1h 低频；单文件库读者重试由 WAL 与既有连接池吸收 |
| 共库容量语义冲突（日志 vs 指标谁先删） | 中 | D5 明确顺序（先日志后指标），并在 API.md 记录；`debug.max_db_size_mb` 与 `metrics.max_rows_total` 各自独立生效、互不掩盖 |
| 行数上限与字节上限不等价（行宽差异） | 低 | 行宽固定（两表字段固定），行数可换算字节；库级字节兜底已存在 |

## 八、交付物

- `backend/app/application/network_service.py`（新建）
- `backend/app/interfaces/http/performance.py` / `network.py`（导出 + 录制端点）
- `backend/app/infrastructure/persistence/sqlite.py` + `domain/ports.py`（指标表与仓储方法）
- `backend/app/application/performance_service.py`（录制钩子 + 生命周期）
- `config/settings.py` + `config/base.yaml`（`metrics` 节）
- 前端 `PerfView.vue` / `NetworkView.vue` / `services/api.ts`
- 测试：`tests/application/`、`tests/unit/http_testkit.py` 风格端点测试、前端 vitest
- 文档：`docs/API.md`、`docs/架构.md`、本方案、`方案/进度追踪.md`

## 九、工作量估算

| 步骤 | 估算 |
|------|------|
| Step 1 NetworkService + HTTP 收敛 | 0.5 人日 |
| Step 2 生命周期治理 | 0.3 人日 |
| Step 3 录制与仓储 | 0.8 人日 |
| Step 4 导出端点 | 0.5 人日 |
| Step 5 清理扩展 | 0.5 人日 |
| Step 6 前端 | 0.5 人日 |
| Step 7 测试与文档 | 0.8 人日 |
| **合计** | **约 4 人日** |

## 参考

- [Perfetto trace buffer 设计（ring buffer / fill_policy）](https://perfetto.dev/docs/design-docs/trace-buffer)：暂存-落盘分离范式
- [Android Studio Profiler：Inspect traces（导出 .trace）](https://developer.android.com/studio/profile/inspect-traces)：会话内存驻留 + 显式导出
- [Prometheus 存储（retention.time / retention.size）](https://prometheus.io/docs/prometheus/latest/storage/)：时间 + 容量双限，超限删最旧 block
- [Netdata dbengine 分级保留](https://learn.netdata.cloud/docs/database)：每档「容量或时间，谁先到算谁」
- [OpenTelemetry Collector file exporter（lumberjack 轮转）](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/fileexporter)：max_megabytes / max_days / max_backups
- [SQLite VACUUM](https://www.sqlite.org/lang_vacuum.html) 与 [auto_vacuum](https://www.sqlite.org/pragma.html#pragma_auto_vacuum)：DELETE 不缩文件、显式回收
- 项目内：[方案 05 清理循环](./05-调试会话管理.md) / [方案 06 日志自动清理](./06-Logcat日志系统.md) / [方案 08 性能监控](./08-性能监控.md)