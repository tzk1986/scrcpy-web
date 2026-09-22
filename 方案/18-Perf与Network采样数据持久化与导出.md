# 18 - Perf / Network 采样数据持久化与导出

## 目标

Perf（CPU/内存/FPS/Activity）与 Network（流量速率/连接数/WiFi）采样数据支持导出为文件，
同时保证磁盘占用可控：

1. **暂存即用即清**：采样期间的暂存数据在采集结束后立即释放，默认不占磁盘；
2. **导出可保留**：导出动作产出可长期保存的文件（CSV/JSON 下载）；
3. **缓存定时清理**：凡是落盘的数据都是"缓存"，定时按时间 + 容量双限清理，保证硬盘容量。

## 状态

- ✅ 已实施（代码 + 测试，2026-09-22）：Step 1-2（NetworkService + 内存生命周期治理）、
  Step 3-4（指标表 + 仓储 + MetricRecorder + 导出/录制端点）、Step 5（清理扩展：
  分段兜底 + 双限 + VACUUM + trim_to_db_size）、Step 6（前端导出按钮/录制开关）、
  Step 7（测试与文档回写）。后端全量 652 passed / 1 skipped（含方案 18 新增
  perf/network 录制状态机 22 例 + HTTP 导出端点 26 例 + 指标仓储 8 例 + 既有用例改造）。
  - ⏳ 待真机冒烟（.18/.33）：录制 10 分钟 → 导出核对 → 停止后暂存释放 → 手工清理验证
    VACUUM → 断网失联停采（验收标准六条逐条核对）
  - ⏳ 待最终推送（经用户确认后）

## 依赖

- [08-性能监控.md](./08-性能监控.md) ✅（Perf 采集/推送/前端展示已落地）
- [14-调试面板其他标签完善.md](./14-调试面板其他标签完善.md) ✅（Perf/Network 标签已落地）
- 既有可复用件：`DatabasePool`（WAL 连接池）、`BatchLogWriter`（批写范式）、
  `DebugService` 清理循环与仓储方法（`delete_old_*` / `trim_logs_to_db_size`）、
  日志导出端点（`StreamingResponse` 范式）、`config/settings.py` 分节配置与热重载
- 需要注意的既有缺口（本方案首次引入，见 B4/S1）：
  - 仓储 `vacuum()`（`infrastructure/persistence/sqlite.py`）已有实现与单测，
    但 **`run_cleanup()` 从未调用它**——VACUUM 从未在清理链路里跑过，本方案是首次接入定时链路；
  - 清理循环 `_cleanup_loop` 无异常隔离（只捕获 `CancelledError`），
    任何异常会让清理任务永久退出；
  - `PerformanceService._sampling_loop` 的 finally **不释放 `_buffers`**，仅 `stop_monitoring` 释放；
  - `lifecycle.py` 关闭段**从未调用** `performance_service.cleanup()`（该方法自落地起是死代码）；
  - `NetworkSampler` 所有采样方法吞异常（`_get_traffic_stats` / `get_connections` / `_get_wifi_status`
    全部 `except Exception` 返回默认值），没有失联信号。

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
3. **空间回收必须显式**（SQLite 事实）：DELETE 只回收逻辑空间，需低频 `VACUUM` 才真正缩文件。
   本仓仓储已有 `vacuum()` 实现与单测，但**尚未接入清理循环**（现状缺口）；本方案首次接入，
   必须按 §3.8 处理其失败面（VACUUM 需独占访问，同库并发写会 `database is locked`）。

## 二、现状（代码事实）

| 侧 | 现状 | 缺口 |
|----|------|------|
| Perf 后端 | `PerformanceService`：每设备内存 `deque(maxlen=3600)` + 1s 采样循环 + WS 订阅分发（`/ws/perf/{device_id}`）；`GET /api/perf/{device_id}/metrics?limit=` 仅读内存；`stop_monitoring` 直接 `_buffers.pop()` 丢弃缓冲；`_sampling_loop` 的 finally 只 pop `_tasks`/`_samplers`/`_subscribers`，**不 pop `_buffers`** | 无导出；停止即全丢（无"保留"通路）；无落盘；离线/自然结束不释放暂存 |
| Network 后端 | 无服务层：`interfaces/http/network.py` 内按设备缓存 `NetworkSampler`（模块级 `_samplers` + `_SAMPLER_TTL = 300s`，为算速率保留上一次采样）；`/stats`、`/connections` 均为即时快照 | 无历史缓冲、无服务、无导出、无落盘；`NetworkSampler` 吞异常无失联信号 |
| 前端 | PerfView 经 WS 收数、仅保留最近 60 点渲染；NetworkView 每 2s HTTP 轮询、仅保留最近 60 点；导出走 `api.exportLogs()`（axios blob + `URL.createObjectURL` + `a.click()`）为 LogcatView 既有范式 | Perf/Network 导出按钮；可选录制开关 |
| 清理 | `DebugService._cleanup_loop` → `run_cleanup()`：删过期日志 + 删过期 shell 历史 + `trim_logs_to_db_size`（超库容量删最旧日志）+ 返回库大小；**无 `vacuum()` 调用**；`_cleanup_loop` 只捕获 `CancelledError` | 清理不覆盖指标数据；无异常隔离 |
| 存储 | `data/debug.sqlite`（DatabasePool + WAL），4 张业务表 `debug_sessions` / `debug_logs` / `shell_history` / `devices` | 无指标表 |

## 三、设计

### 3.1 数据生命周期（三态）

| 态 | 载体 | 产生条件 | 生命周期 | 清理者 |
|----|------|----------|----------|--------|
| **暂存态** | 内存 ring buffer（每设备） | 监控运行中（WS 活跃 / 显式 start / 录制中） | 显式停止监控 / 空闲 TTL 到期停采 / 设备失联（含服务关闭）→ **立即释放** | 服务自身：`stop_monitoring` 与 `_sampling_loop` 的 finally 统一走 `_release_device(device_id)` |
| **缓存态** | SQLite `perf_samples` / `network_samples` | 仅"录制"开启时落盘（显式动作） | 落盘后保留，供导出与回溯 | 定时清理任务（时间 + 容量双限 + 低频 VACUUM，见 §3.8） |
| **交付态** | 浏览器下载文件 | 用户点"导出" | 用户自行保管 | 服务端零占用（流式下载，不留临时文件） |

对应三条目标：暂存态满足"执行完成即清除、不占空间"；缓存态 + 交付态满足"导出时能保留数据"；
定时清理覆盖缓存态，满足"保证硬盘容量"。

**用户提示**：缓存态与交付态都要求先开启"录制"；未开录制时导出仅能取 `source=buffer`
（内存快照，停止监控即丢失）——UI 在导出对话框中显式提示该语义（见 Step 6）。

### 3.2 关键决策

- **D1 默认不落盘**：`metrics.recording` 默认 `false`。不录制时指标表零写入，内存环缓冲有硬上限，
  停止监控即释放——直击"不占空间"诉求（Perfetto/Profiler 范式）。
- **D2 导出两个来源**：`source=buffer`（默认，内存暂存快照，**零磁盘，任何时刻可用**）与
  `source=cache`（缓存态区间，需曾录制；支持 `from`/`to` 过滤）。导出为流式下载
  （`StreamingResponse` + `Content-Disposition`），复用 logcat 导出范式。
  `source=buffer` 时 `from`/`to` 被忽略（缓冲内全量，受 `limit` 截断最新 limit 条）。
- **D3 双限清理**：`metrics.retention_days`（默认 **3 天**）+ `metrics.max_rows_total`
  （默认 **50 万行**，Perf+Network 合计），两条件 OR，超限**按 ts 删最旧**（分批 DELETE）。
  容量用**行数**而非字节：行数可直接观测、不依赖 `dbstat`；库级字节兜底沿用
  `debug.max_db_size_mb`（见 D5）。默认值试算见 §3.5。
- **D4 删除后低频 VACUUM**：仅在本次清理确有删除时调用（复用仓储 `vacuum()`），
  避免每次空转重建库；VACUUM 锁库与失败面按 §3.8 处理（分段兜底，失败不影响后续清理）。
- **D5 共库与兜底顺序**：指标与日志同库 `data/debug.sqlite`。库级超限（`debug.max_db_size_mb`）
  的裁剪顺序为 **指标 → shell 历史 → 日志**（各按 ts 删最旧），指标自身再受 D3 约束。
  理由：指标是**可再生采样**（设备在线即可重采），日志是主业务数据（不可再生）——先删指标，
  与 D3「指标自带行数上限」的意图一致。
  注意 WAL 计量陷阱：`get_db_size_bytes()` 只 stat 主库文件，WAL 下 DELETE 的效果仅在
  checkpoint 后反映；既有测试已证明超限裁剪会删到表空。语义扩展到跨表后，极端情况下
  「库级超限 → 指标被删光」属可接受结果，但必须写进 `docs/API.md`。
- **D6 界面缩略与后端解耦**：前端 60 点仅用于绘图，导出走后端缓冲（最多 `buffer_size` 点），
  前端不需要为导出增大内存。

**不做**（YAGNI）：分级降采样（Netdata tier 范式，数据量未到需要时）、独立 TSDB、
服务端导出文件保留目录（流式即弃，无需清理）、指标聚合报表、录制批次表（见 S13 的口径替代）。

### 3.3 API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/perf/{device_id}/export` | `?format=csv\|json&source=buffer\|cache&from=&to=&limit=` → 附件下载 |
| GET | `/api/network/{device_id}/export` | 同上（字段为网络指标） |
| POST | `/api/perf/{device_id}/record/start` | 开启录制（幂等；`metrics.recording=false` 时返回 400 `RECORDING_DISABLED`，不静默） |
| POST | `/api/perf/{device_id}/record/stop` | 停止录制（幂等；返回落盘行数） |
| GET | `/api/perf/{device_id}/record/status` | `{recording, reason, rows, oldest_ts, newest_ts}` |
| POST | `/api/network/{device_id}/record/start\|stop`、GET `.../record/status` | 对称 |

约定：

- 文件名净化：`attachment; filename="perf_{device_id.replace(':','_')}_{ts}.csv"`
  （device_id 含 `:`，如 `192.168.8.18:5555`，直接拼入在 Windows 上非法）；
- `limit` 默认 50000、**上界 200000**（越界 400）；`source=cache` 单次导出内存峰值
  ≈ limit × 行宽（见风险表量级）；
- 空数据：`source=buffer` 无点或 `source=cache` 无区间数据时返回 **404** `NO_DATA`
  （前端据此提示"无数据可导出"，避免下载到空文件/错误 JSON）；
- `ts` 语义：epoch 秒（UTC 基准）；CSV 增加 `ts_iso` 列（本地时区 ISO8601）便于人工核对；
- `record/status` 的 `oldest_ts/newest_ts` 为**跨批次聚合值**（多次录制叠加），
  精确单次区间请以 stop 时刻返回的值为准（不做批次表，YAGNI）；
- 导出 `source=cache` 前先 `flush()` 录制批写缓冲（见 S14 边界）。

既有端点（`GET /api/perf/{device_id}/metrics`、`/api/network/{device_id}/stats|connections`）
**字段与状态码不变**；`/stats` 的 `ts` 语义由"请求时刻"改为"最近一次采样时刻"
（采样间隔口径，见 Step 1 冷启动策略）。

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
CREATE INDEX IF NOT EXISTS idx_perf_samples_ts ON perf_samples(ts);

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
CREATE INDEX IF NOT EXISTS idx_network_samples_ts ON network_samples(ts);
```

- `(device_id, ts)` 服务按设备区间导出；`(ts)` 服务**全局按 ts 裁剪**（`trim_metrics_rows` /
  `count_metrics_rows` 是全局排序，走不到复合索引首列，需单列 ts 索引）；
- 写入走批量（100 条 / 100ms，复用 `BatchLogWriter` 范式，`executemany` 单事务）；
- `NetworkSampler.get_connections` 的连接明细**不入库**（属于即时快照，非时间序列）；
- `count_metrics_rows()` 语义 = 两表行数之和。

**合并裁剪（`trim_metrics_rows(max_rows)`）伪码**（两表合计删最旧）：

```
total = count_metrics_rows()            # 两表之和
if total <= max_rows: return 0
excess = total - max_rows

# 阈值法：两表各取按 ts 升序的前 cluster 条（走 idx_*_ts），归并定位第 excess 条
batch = 1000
candidates = merge_sorted(perf_head(batch), network_head(batch))   # 升序 (table, ts)
if len(candidates) < excess: 按更大的 batch 重新取头（最多重试到 excess 覆盖）
cut_ts = candidates[excess - 1].ts

deleted = delete_perf_where_ts_lt(cut_ts) + delete_network_where_ts_lt(cut_ts)
# 同 ts 边界可能多出，补删 equals 部分多余行至 deleted == excess（按 rowid 顺序分批 1000）
return deleted
```

（实施时如阈值法边界复杂，允许退化为「两表各取头按 ts 归并、逐步 DELETE 到 excess」，
语义一致，仅性能略差。）

### 3.5 配置新增

```yaml
metrics:
  buffer_size: 3600           # Perf 内存暂存条数（@1s 采样约 1 小时/设备）
  network_buffer_size: 1800   # Network 内存暂存条数（@2s 采样约 1 小时/设备）
  network_interval: 2.0       # Network 采样间隔（秒）
  idle_ttl_seconds: 300       # 无订阅者且未录制时的空闲停采宽限（秒）
  lost_failures: 3            # 连续采样失败阈值 → 判定设备失联，停采并释放缓冲
  recording: false            # 录制（落盘暂存）总开关，默认关闭
  retention_days: 3           # 缓存态保留期（天）
  max_rows_total: 500000      # 缓存态容量上限（行，Perf+Network 合计）
```

清理周期沿用 `debug.cleanup_interval_hours`（默认 1h），不新增节拍。
所有键进新 `MetricsConfig` 并支持配置热重载（运行时读取，见方案 01）。

**热重载边界（S3）**：`deque(maxlen=)` 构造后不可调整——`buffer_size` / `network_buffer_size`
的热重载**仅对新会话生效**（已存在的缓冲保持构造时上限，`stop_monitoring` 后重新 start 才应用新值）；
现状 `MAX_BUFFER_SIZE = 3600` 类常量与 `interfaces/http/performance.py` 中 limit 校验硬编码
`1..3600` 一并改为启动时从 `settings().metrics.buffer_size` 读取（limit 上界 = 当前配置值）。

**默认值试算（S4）**：

- Perf @1s ≈ 86400 行/设备/天；Network @2s ≈ 43200 行/设备/天 → **单设备约 13 万行/天**；
- 单设备 3 天 ≈ 38.9 万行（在 50 万内）；**双设备连续录制约 1.9 天触顶**，
  此后 `retention_days=3` 不再生效、行数限成为唯一生效条件——这是"双限谁先到算谁"的正常表现，
  但需在 `docs/API.md` 写明；如需双设备满 3 天，建议 `max_rows_total` 提到 100 万；
- 行数↔体积：含 `current_activity` / `top_package` / `wifi_ssid` 变长列与页开销，
  50 万行 ≈ **50–75MB**；而 `debug.max_db_size_mb` 默认 2048MB → 库级字节兜底几乎不会被指标触发
  （D5 兜底仅覆盖极端场景，属预期）。

**内存量级（S11）**：Perf 3600 点 × 约 0.25KB/点 ≈ **0.9MB/设备**；Network 1800 点 ≈ **0.5MB/设备**；
100 设备同时被观看（各含 1 采样任务 + ADB 子进程）约 **140MB 常驻**——项目目标 100+ 设备下可接受，
但需在风险表记录。导出峰值：`source=cache` + limit=50000 ≈ 7.5MB 原始字符 + 对象开销 ≈ **20–30MB**，
低于上界 200000（≈ 80–120MB）；如需更大范围导出，后续再评估分块/游标方案（YAGNI）。

### 3.6 录制状态机（B3）

录制依附采样循环写批量缓冲，必须与"空闲停采 / 离线 / 热重载 / 重启"对齐：

| 事件 | 采样循环 | 录制状态 | 处理 |
|------|---------|---------|------|
| `record/start`（监控未运行） | **拉起** `start_monitoring` | running | 录制隐含启动采样循环（否则采不到点） |
| WS 订阅全断开 | — | running | **录制中禁止空闲停采**：空闲守卫复查条件含"无录制" |
| 空闲 TTL 到期（无订阅且无录制） | `stop_monitoring`（释放缓冲） | — | 空闲守卫任务，见 Step 2 |
| 设备失联（连续 `lost_failures` 次失败） | 自然结束（finally） | → stopped(`reason="device_lost"`) | final flush 已落盘；status 反映 |
| 设备重连 | 需新的 WS / start / record/start | stopped | **不自动恢复**（显式优于隐式），UI 提示可重开 |
| 显式 `stop_monitoring` / `record/stop` | 停 | → stopped(`reason="stopped"`) | final flush |
| `metrics.recording` 热重载 true→false | 停采 | 全部 → stopped(`reason="config"`) | 结构化日志 + status 反映；false→true 不自动拉起 |
| `metrics.recording=false` 时 `record/start` | — | 不变 | 400 `RECORDING_DISABLED`（不静默） |
| 服务关闭 | `cleanup()` → stop_monitoring | → stopped(`reason="shutdown"`) | final flush 挂 `cleanup()`（见 §3.7） |
| 进程重启 | — | 运行态丢失 | status 返回 `recording:false` + `rows/oldest_ts/newest_ts` 供回溯 |

### 3.7 生命周期与关闭钩子（B2）

现状 `lifecycle.py` 关闭段只有 `config_watcher.stop()` / `stop_cleanup_task()` / `writer.flush()`，
`performance_service.cleanup()` 是死代码。本方案新增：

- `PerformanceService.cleanup()` 扩展：`stop_monitoring` 全设备 + **录制器 final flush**；
- 新增 `NetworkService.cleanup()`：同样停采 + 释放缓冲；
- `lifecycle.py` 关闭段（`writer.flush()` 之前）新增：
  `await performance_service.cleanup()`、`await network_service.cleanup()`（均幂等）；
- `deps.py` 新增 `get_network_service()` 单例（与既有 `get_performance_service()` 同风格）；
- 启动段（`yield` 之前）无需变更：Network 采样按需启动（订阅/HTTP 首个请求触发）。

### 3.8 清理链路健壮性（B4）

`run_cleanup()` 追加指标清理后，任一环节抛异常（尤其 VACUUM 的 `database is locked`）
会击穿"硬盘容量保证"，因此：

- `run_cleanup()` 内部**分段 try/except**：保留期删除（日志）/ 保留期删除（shell 历史）/
  `delete_old_metrics` / `trim_metrics_rows` / `trim_to_db_size` / `vacuum` 各自兜底，
  失败记结构化告警（`cleanup_step_failed` + step 名）并继续后续段；
- 返回字典新增各段结果与 `errors` 列表（含 step 名与错误串），`cleanup_completed` 日志同步；
- `_cleanup_loop` 外层再加一层 `except Exception` → 告警后续跑（**循环不退出**）；
- VACUUM 失败不影响本轮已完成的 DELETE，也不影响后续轮次（下轮有删除时会再试）；
- 风险表相应改写：WAL 不豁免 VACUUM 的独占要求，失败面由分段兜底吸收。

## 四、实施步骤

### Step 1：NetworkService（暂存层补齐）

`backend/app/application/network_service.py`（新建，应用层）：
- 每设备采样循环（`network_interval`，默认 2s）+ `deque(maxlen=network_buffer_size)` 环形缓冲；
- **失联判定（B1）**：`NetworkSampler` 现状吞异常无信号，需增加"不吞错的采样入口"
  （建议 `get_stats(device_id, strict=True)` 参数，strict 时异常上抛；HTTP 层默认 `strict=False`
  保持既有行为不变），采样循环 strict 模式计连续失败，达 `lost_failures` → 停采 + 释放缓冲
  + 录制置 stopped(`device_lost`)；
- 订阅分发（供未来 WS 化，当前前端仍走 HTTP 轮询，可不暴露订阅）；
- `interfaces/http/network.py` 收敛：模块级 `_samplers` / `_SAMPLER_TTL` 语义上移（HTTP 层无状态），
  `/stats`、`/connections` 经 service 读取；
- **冷启动策略（S6）**：请求到达且缓冲无新鲜点（`age >= network_interval`）时**内联采一次并返回**
  （请求多等约 0.5–1s，4–5 条 shell）；`age < network_interval` 直接复用缓冲最新点。
  首包速率必为 0（NetworkSampler 需两次采样算速率，属既有语义），文档写明；
- 停止/设备失联 → 释放缓冲（统一 `_release_device`，对齐 Perf）。

### Step 2：内存生命周期治理（暂存态清零）

- **Perf `_sampling_loop` finally 补 `self._buffers.pop(device_id, None)`**（B1：离线/自然结束
  路径同样释放暂存），并统一抽 `_release_device(device_id)` 供 `stop_monitoring` 与 finally 共用；
- Perf 补**空闲守卫**（O3 引用计数语义）：`stream_metrics` 断开且 `_subscribers[device_id]` 为空
  且无录制时，启动 per-device 空闲任务，`idle_ttl_seconds` 后**复查**（仍无订阅者且无录制）
  才走 `stop_monitoring`（含 buffer pop）；新订阅/新录制到来即取消守卫。**不走 `sample()`
  自然结束路径**（该路径释放语义不同）；
- Network：同规则（守卫 + `idle_ttl_seconds`）；
- 服务关闭：`lifecycle.py` 新增 `performance_service.cleanup()` / `network_service.cleanup()`
  调用（§3.7），含录制 final flush。

### Step 3：录制（缓存态落盘）

- Schema 建表（`infrastructure/persistence/sqlite.py` 初始化处幂等 `CREATE TABLE IF NOT EXISTS`
  + 4 个索引，见 §3.4）；
- 仓储方法（`domain/ports.py` Protocol + SQLite 实现）：
  `save_perf_samples_bulk` / `save_network_samples_bulk`、`delete_old_metrics(retention_seconds)`、
  `trim_metrics_rows(max_rows)`（§3.4 伪码）、`count_metrics_rows()`（两表之和）；
- 录制器 `MetricRecorder`（infrastructure 层，复用 `BatchLogWriter` 范式：100 条 / 100ms 单事务）：
  挂 `PerformanceService._recorders[device_id]`；`record/start` 幂等（含拉起采样循环）；
  `record/stop` / 失联 / 关闭 → `final_flush()` 并返回落盘行数；
- `metrics.recording` 总开关热重载语义按 §3.6 状态机（true→false 时全部停）；
- `record/status` 返回 `{recording, reason, rows, oldest_ts, newest_ts}`，重启后运行态显式 `false`。

### Step 4：导出端点

- `interfaces/http/performance.py` / `network.py` 各加 export 端点；
- `source=buffer`：序列化 ring buffer 快照（`from`/`to` 忽略）；`source=cache`：按
  `device_id + ts 区间` 查库，**查询前先 `flush()` 录制批写缓冲**（S14）；
- CSV 表头：Perf = `ts,ts_iso,cpu_percent,total_memory_mb,used_memory_mb,fps,jank_count,current_activity,top_package`；
  Network = `ts,ts_iso,rx_bytes,tx_bytes,rx_rate_kbps,tx_rate_kbps,active_connections,wifi_connected,wifi_ssid`；
- JSON 与 logcat 导出一致（对象数组，`ts` 为 epoch 秒）；
- 上限保护：`limit` 默认 50000、上界 200000；文件名净化与 404 `NO_DATA` 见 §3.3。

### Step 5：清理扩展

- `run_cleanup()` 分段兜底（§3.8）后追加：`delete_old_metrics(retention_days) →
  trim_metrics_rows(max_rows_total) → 有删除才 vacuum()`；
- 库级兜底：`trim_logs_to_db_size` 重命名/扩展为跨表 `trim_to_db_size`
  （顺序：**指标 → shell 历史 → 日志**，D5）；
- 清理结果字典加 `metrics_deleted` / `metrics_trimmed` / `errors` 字段，日志同步；
- **影响面清单（S5）**：`domain/ports.py`（Protocol 定义）、
  `infrastructure/persistence/sqlite.py`（实现）、`application/debug_service.py`（唯一调用方）、
  `tests/infrastructure/test_sqlite_repo.py`（4 处直接调用并断言）、`docs/架构.md`、`docs/API.md`。

### Step 6：前端

- `PerfView.vue` / `NetworkView.vue` 工具栏加：导出按钮（**与 LogcatView 一致的 blob 范式**：
  `api.exportPerfMetrics()` axios `responseType:'blob'` + `URL.createObjectURL` + `a.click()`，
  非 `window.open`——后者无法感知 4xx/5xx 会把错误 JSON 当文件下载）、
  格式选择（CSV/JSON）、录制开关 + 录制中徽标（沿用 LIVE 指示器样式）；
- 导出成功/失败/空数据（404 `NO_DATA`）提示；导出完成后回显行数与区间（O2）；
- `api.ts` 加 `exportPerfMetrics` / `exportNetworkStats` / `startRecording` / `stopRecording` /
  `getRecordStatus`。

### Step 7：测试与文档

- 后端单测：缓冲上限与淘汰、**离线/空闲停采后缓冲为空（B1 验收）**、停止释放、
  录制批量写入与 final flush、录制状态机各转移（§3.6 每条一行）、导出 CSV/JSON 内容与
  Content-Disposition 与文件名净化、404 无数据、清理（时间删除 / 行数裁剪 / 无删除不 VACUUM /
  **VACUUM 失败循环存活且续跑（B4）**）、source=cache 区间过滤与 flush 边界、
  `metrics.recording=false` 时录制端点 400；
- **既有用例改造（S5）**：`tests/unit/test_http_network.py`（围绕 `_get_sampler` 复用/TTL/
  直连 /stats 行为，Step 1 语义上移后必然要重写）、`tests/infrastructure/test_sqlite_repo.py`
  （`trim_logs_to_db_size` 重命名影响）、新增 `tests/application/test_network_service.py`、
  `tests/unit/test_http_perf_export.py`；
- 前端 vitest：导出按钮触发与 blob 下载调用、录制开关状态流转、录制徽标、空数据提示；
- 真机冒烟（.18/.33）：录制 10 分钟 → 导出核对行数与数值（`ts_iso` 对照）→ 停止后暂存释放
  （缓冲为空 + 库大小）→ 手工触发清理验证删除与 VACUUM → 断网验证失联停采与录制停止；
- 文档回写：`docs/API.md`（新端点、双限谁先到算谁、WAL 字节兜底删光语义、D5 顺序、
  手动 cleanup 与录制并发说明 O4）、`docs/架构.md`（NetworkService、指标表、
  **修正 :354 "清理含 vacuum" 的陈旧描述**）、`docs/用户手册.md`（导出/录制用户可见说明）、
  本方案状态、`方案/进度追踪.md`；
- 与方案 15（exe 打包）交互（O7）：打包场景沿用 `database.path` 配置定位 `data/`，不做额外处理。

## 五、测试策略

| 层级 | 覆盖点 |
|------|--------|
| 单元（pytest） | ring buffer 上限/淘汰、离线/空闲停采释放、录制状态机、批写与 final flush、导出两来源与格式与空数据、清理双限 + 分段兜底 + VACUUM 条件与失败续跑、热重载读取新配置、冷启动内联采样 |
| 前端（vitest） | 按钮/开关/徽标交互，blob 下载调用，错误与空数据提示 |
| 真机（手工，.18/.33） | 采样精度对照（与 `dumpsys`/`/proc/net/dev` 手工取值核对）、录制-导出-清理全链路、失联停采、磁盘占用前后对比 |

## 六、验收标准

- [ ] 不录制时 `perf_samples` / `network_samples` 行数恒为 0；采样 10 分钟内
      `data/debug.sqlite` 与 `-wal` 体积无增长（排除并发调试会话的日志写入）
- [ ] 停止监控 / 空闲 TTL 到期 / 设备失联 / 服务关闭后，Perf 与 Network 缓冲均为空（可断言）
- [ ] 导出在 buffer 与 cache 两来源下均产出正确 CSV/JSON（含 `ts_iso`、文件名已净化），
      空数据返回 404 `NO_DATA`，服务端无临时文件残留
- [ ] 缓存态数据超 `retention_days` 或 `max_rows_total` 时被清理（先删最旧），
      且删除后库文件体积下降（VACUUM 生效）
- [ ] VACUUM 失败（模拟 `database is locked`）时该轮清理其余步骤仍完成、清理循环存活并续跑
- [ ] `metrics.recording` 关闭时录制端点返回 400 `RECORDING_DISABLED`（不静默）；
      录制中切走页面（无订阅）采样循环不停止
- [ ] 既有 Perf/Network 实时展示与 `/metrics`、`/stats` 字段与状态码不回归
- [ ] 门禁：pytest（含覆盖率 ≥80%）、ruff、mypy strict、前端 vitest 全绿

## 七、风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| ADB 采样开销叠加（top/dumpsys 每条命令进程开销） | 中 | Network 默认 2s 且复用缓冲新鲜点（冷启动仅内联一次）；Perf 维持现有 1s；多设备时按需降频（配置项已预留） |
| SQLite 写放大（录制时的持续写入） | 中 | 批量写（100 条/100ms 单事务）；默认关闭录制 |
| VACUUM 锁库（需独占访问，WAL 不豁免） | 中 | 仅在有删除时执行；分段兜底（§3.8）保证失败不击穿清理循环；清理周期 1h 低频 |
| 共库容量语义冲突（日志 vs 指标谁先删） | 中 | D5 明确顺序（指标 → shell 历史 → 日志）并写进 API.md；`debug.max_db_size_mb` 与 `metrics.max_rows_total` 各自独立生效、互不掩盖；WAL 下字节兜底可能删光最低优先级数据的语义已写明 |
| 行数上限与字节上限不等价（行宽差异） | 低 | 量级估算已给（50 万行 ≈ 50–75MB）；库级字节兜底已存在 |
| 多设备内存量级（100+ 设备目标） | 低-中 | 100 设备同时观看约 140MB 常驻（已量化）；空闲停采保证非常看设备不驻留 |
| 双限默认值在双设备下时间限先失效（S4） | 低 | 试算已写明；需要更长保留时调 `max_rows_total`（推荐 100 万） |

## 八、交付物

- `backend/app/application/network_service.py`（新建）
- `backend/app/application/performance_service.py`（`_release_device` / 空闲守卫 / 录制钩子 / `cleanup()`）
- `backend/app/infrastructure/persistence/metric_recorder.py`（新建，批写录制器）
- `backend/app/infrastructure/network/sampler.py`（`strict` 采样入口）
- `backend/app/interfaces/http/performance.py` / `network.py`（导出 + 录制端点；limit 上界配置化）
- `backend/app/infrastructure/persistence/sqlite.py` + `backend/app/domain/ports.py`
  （指标表、索引与仓储方法、`trim_to_db_size`）
- `backend/app/deps.py`（NetworkService 注册）、`backend/app/lifecycle.py`（关闭钩子）
- `config/settings.py` + `config/base.yaml`（`metrics` 节，含 `idle_ttl_seconds` / `lost_failures`）
- 前端 `frontend/src/components/debug/PerfView.vue` / `NetworkView.vue` / `services/api.ts`
- 测试：`tests/application/test_performance_service.py`（扩展）、`tests/application/test_network_service.py`（新建）、
  `tests/unit/test_http_perf_export.py`（新建）、`tests/unit/test_http_network.py`（重写）、
  `tests/infrastructure/test_sqlite_repo.py`（改造 + 指标仓储用例）、前端 vitest
- 文档：`docs/API.md`、`docs/架构.md`（含 :354 修正）、`docs/用户手册.md`、本方案、`方案/进度追踪.md`

## 九、工作量估算

| 项 | 估算 |
|----|------|
| 功能实现（Step 1-6：NetworkService / 生命周期 / 录制 / 导出 / 清理 / 前端） | 3.5 人日 |
| 测试与既有用例改造（Step 7 后端/前端 + `test_http_network.py` 重写 + 覆盖率匹配现状 89.5% 门禁） | 1.5-2 人日 |
| 真机验证（.18/.33 全链路冒烟）与文档回写 | 1 人日 |
| **合计** | **5.5-7 人日** |

## 更新记录

| 日期 | 版本 | 内容 |
|------|------|------|
| 2026-09-22 | v1.0 | 调研 + 设计初稿 |
| 2026-09-22 | v1.1 | 按独立审查修订：补 B1-B4（离线/空闲释放缓冲、lifecycle 关闭钩子、录制状态机、清理异常隔离）；采纳 S1-S14（VACUUM 措辞与架构文档修正、D5 顺序、配置化 limit、默认值试算、测试改造清单、冷启动语义、blob 导出范式、索引与裁剪伪码、交付物补齐、工作量上调、内存量级、验收口径、ts 时区、flush 边界）与 O1-O5/O7；`docs/架构.md:354` 待随实施修正 |

## 参考

- [Perfetto trace buffer 设计（ring buffer / fill_policy）](https://perfetto.dev/docs/design-docs/trace-buffer)：暂存-落盘分离范式
- [Android Studio Profiler：Inspect traces（导出 .trace）](https://developer.android.com/studio/profile/inspect-traces)：会话内存驻留 + 显式导出
- [Prometheus 存储（retention.time / retention.size）](https://prometheus.io/docs/prometheus/latest/storage/)：时间 + 容量双限，超限删最旧 block
- [Netdata dbengine 分级保留](https://learn.netdata.cloud/docs/database)：每档「容量或时间，谁先到算谁」
- [OpenTelemetry Collector file exporter（lumberjack 轮转）](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/fileexporter)：max_megabytes / max_days / max_backups
- [SQLite VACUUM](https://www.sqlite.org/lang_vacuum.html) 与 [auto_vacuum](https://www.sqlite.org/pragma.html#pragma_auto_vacuum)：DELETE 不缩文件、显式回收
- 项目内：[方案 05 清理循环](./05-调试会话管理.md) / [方案 06 日志自动清理](./06-Logcat日志系统.md) / [方案 08 性能监控](./08-性能监控.md)