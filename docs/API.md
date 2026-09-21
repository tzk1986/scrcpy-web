# API 参考（HTTP + WebSocket + 二进制协议）

> 本文档是 OpenScrcpy 后端的接口契约清单，面向集成者与后续开发者。
> 所有路径、参数名、字段名均逐条核对自当前代码；如与实现冲突，以代码为准。
>
> 核对来源：
> - HTTP：`backend/app/interfaces/http/{devices,apps,debug,network,performance,sessions}.py`
> - 路由装配：`backend/app/main.py`（`include_router` 与内联 `@app.websocket` 注册）
> - WebSocket：`backend/app/interfaces/ws/{video,debug,performance}.py`
> - 视频流协议：`backend/app/scrcpy/stream_protocol.py`、`backend/app/scrcpy/au_aggregator.py`
> - 控制协议：`backend/app/scrcpy/control_sender.py`、`backend/app/infrastructure/stream/scrcpy.py`
> - 前端调用方：`frontend/src/services/api.ts`、`stores/debug.ts`、`stores/device.ts`、`components/debug/PerfView.vue`、`components/stream/VideoPlayer.vue`

## 一、概览

### 1.1 地址与端口

| 项 | 值 | 来源 |
|----|----|------|
| 后端监听 | `0.0.0.0:8765`（HTTP 与 WebSocket 同端口） | `config/base.yaml` `server.host/port`；`config/settings.py` `ServerConfig` |
| 端口覆盖 | 环境变量 `BACKEND_HOST` / `BACKEND_PORT`（优先级最高） | `config/settings.py:get_settings()` |
| 前端 dev server | `http://localhost:8080`，`/api` 与 `/ws` 由 Vite 代理到后端 | `frontend/vite.config.ts` |
| API 前缀 | HTTP 业务端点为 `/api/*`；WS 为 `/ws/*`；另有根路径 `/health` | `backend/app/main.py` |
| 交互式文档 | `/docs`（Swagger UI）、`/redoc`、`/openapi.json`（FastAPI 自动生成） | `FastAPI(title=..., version=...)` |

浏览器端不直接访问 8765，而是通过前端 dev server（或生产托管）同源代理访问 `/api`、`/ws`。

### 1.2 通用约定

- 所有 HTTP 请求/响应体为 UTF-8 JSON（截图与文件导出例外，见对应端点）。
- 无鉴权：当前所有端点不校验身份（协作会话的 `user_id` 只是标识符，不是凭证）。
- 无分页：列表类端点以 `limit` 截断（见各端点）。
- HTTP 方法语义：查询用 `GET`，变更用 `POST`，删除用 `DELETE`；集合创建端点（`POST /api/devices/connect` 等）参数放在查询串中。

### 1.3 CORS

由 `settings().security.cors_origins` 驱动（`SecurityConfig` 默认 `["http://localhost:8080"]`，与 `config/base.yaml` / `config/dev.yaml` 一致），`allow_credentials=True`、`allow_methods=["*"]`、`allow_headers=["*"]`。开发环境经 Vite 代理同源访问，通常不触发 CORS。

### 1.4 错误格式

后端注册了四个异常处理器（`backend/app/main.py` → `backend/app/core/exceptions.py`），**所有错误响应统一为 `{"error": {"code", "message"}}` 形状**（2026-09-21 契约统一）：

| 场景 | HTTP 状态码 | 响应体 |
|------|------------|--------|
| 领域异常（`OpenScrcpyException` 子类） | `exc.status_code`（默认 400；未找到 404、无权限 403） | `{"error": {"code": "<CODE>", "message": "<描述>"}}` |
| 端点内 `raise HTTPException(...)` | 该状态码 | `{"error": {"code": "HTTP_ERROR", "message": "<detail>"}}` |
| 请求参数校验失败 | 422 | `{"error": {"code": "VALIDATION_ERROR", "message": "<字段: 原因; ...>"}}` |
| 未捕获异常（兜底，消息不外泄） | 500 | `{"error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred"}}` |

领域错误码取值（`backend/app/core/exceptions.py`）：

| code | HTTP | 触发条件 |
|------|------|---------|
| `DEVICE_NOT_FOUND` | 404 | 设备 ID 未连接/不在仓库 |
| `SESSION_NOT_FOUND` | 404 | 会话 ID 无效（调试会话与协作会话共用） |
| `PERMISSION_DENIED` | 403 | 权限不足（如非 admin 转移控制权） |
| `ADB_ERROR` | 400 | ADB 子进程非零退出 |
| `DEVICE_UNREACHABLE` | 400 | `connect`/`disconnect` 前置 TCP 可达性预检失败 |
| `UNKNOWN_ERROR` | 400 | 基类默认值 |

## 二、HTTP 端点

### 2.1 端点总览

| 方法 | 路径 | 说明 | 前端调用方 |
|------|------|------|-----------|
| GET | `/health` | 健康检查 | —（容器/脚本） |
| GET | `/api/devices` | 设备列表 | `api.listDevices` |
| GET | `/api/devices/{device_id}` | 单设备信息 | `api.getDevice` |
| POST | `/api/devices/connect` | TCP/IP 连接设备 | `api.connectDevice` |
| POST | `/api/devices/{device_id}/disconnect` | 断开 TCP/IP 连接 | `api.disconnectDevice` |
| POST | `/api/devices/{device_id}/install` | 安装 APK | `api.installApk` |
| POST | `/api/devices/batch/install` | 批量安装 APK | — |
| GET | `/api/devices/{device_id}/screenshot` | 截图（PNG） | `api.screenshot` |
| GET | `/api/devices/events` | 设备上下线事件流（SSE） | `stores/device.ts:startSSE` |
| GET | `/api/apps/{device_id}` | 应用列表 | `api.listApps` |
| GET | `/api/apps/{device_id}/{package}` | 应用详情 | `api.getAppInfo` |
| GET | `/api/apps/{device_id}/{package}/memory` | 应用内存占用 | `api.getAppMemory` |
| POST | `/api/apps/{device_id}/{package}/launch` | 启动应用 | `api.launchApp` |
| POST | `/api/apps/{device_id}/{package}/stop` | 强制停止 | `api.stopApp` |
| POST | `/api/apps/{device_id}/{package}/uninstall` | 卸载 | `api.uninstallApp` |
| POST | `/api/apps/{device_id}/{package}/clear-data` | 清除数据 | `api.clearAppData` |
| POST | `/api/debug/sessions` | 创建调试会话 | `api.createDebugSession` |
| GET | `/api/debug/sessions/{session_id}` | 会话信息 | `api.getDebugSession` |
| DELETE | `/api/debug/sessions/{session_id}` | 关闭会话 | `api.closeDebugSession` |
| GET | `/api/debug/sessions/{session_id}/logs` | 查询日志 | `api.getLogs` |
| GET | `/api/debug/sessions/{session_id}/logs/export` | 导出日志 | `api.exportLogs` |
| DELETE | `/api/debug/sessions/{session_id}/logs` | 清空会话日志 | `api.cleanupSessionLogs` |
| POST | `/api/debug/sessions/{session_id}/shell` | 执行 shell（同步） | `api.execShell` |
| POST | `/api/debug/cleanup` | 手动触发日志清理 | `api.runCleanup` |
| GET | `/api/debug/stats` | 调试系统统计 | `api.getDebugStats` |
| GET | `/api/perf/{device_id}/metrics` | 历史性能指标 | `api.getPerfMetrics` |
| POST | `/api/perf/{device_id}/start` | 启动性能监控 | `api.startPerfMonitoring` |
| POST | `/api/perf/{device_id}/stop` | 停止性能监控 | `api.stopPerfMonitoring` |
| GET | `/api/network/{device_id}/stats` | 网络统计 | `api.getNetworkStats` |
| GET | `/api/network/{device_id}/connections` | 活跃连接列表 | `api.getNetworkConnections` |
| POST | `/api/sessions` | 创建协作会话 | — |
| GET | `/api/sessions/{session_id}` | 协作会话信息 | — |
| POST | `/api/sessions/{session_id}/join` | 加入协作会话 | — |
| POST | `/api/sessions/{session_id}/leave` | 离开协作会话 | — |
| POST | `/api/sessions/{session_id}/transfer` | 转移控制权 | — |

### 2.2 系统

#### GET /health

健康检查，供 Docker HEALTHCHECK / 容器编排器使用。

- 参数：无
- 响应 200：`{"status": "ok"}`

### 2.3 设备（`/api/devices`）

#### GET /api/devices

列出所有通过 ADB 连接的设备（实时扫描 ADB 并 upsert 到仓库）。

- 参数：无
- 响应 200：`DeviceInfo[]`

```json
[
  {
    "id": "192.168.1.33:5555",
    "model": "Pixel 6",
    "os_version": "13",
    "resolution": [1080, 1920],
    "battery": 87,
    "status": "online",
    "ip": null,
    "port": null
  }
]
```

字段说明（`backend/app/domain/device.py`）：`status` 取 `"online" | "offline" | "busy"`；`resolution` 为 `[宽, 高]` 数组；`ip`/`port` 为可选字段，当前 ADB 驱动（`infrastructure/adb/cli.py:get_device_info`）不填充，恒为 `null`。单台设备信息获取失败时该设备被跳过，不影响整体列表。

#### GET /api/devices/{device_id}

- 路径参数：`device_id` — ADB 序列号（从本地仓库查询）
- 响应 200：`DeviceInfo` 对象
- 错误：404 `DEVICE_NOT_FOUND`（设备不存在）

#### POST /api/devices/connect

通过 TCP/IP 连接设备（`adb connect` + TCP 可达性预检）。

- 查询参数：`ip`（必填，设备 IP）、`port`（可选，默认 `5555`）
- 响应 200：`{"success": true, "device_id": "192.168.1.33:5555"}`
- 错误：400 `DEVICE_UNREACHABLE`（预检失败）、400 `ADB_ERROR`

前端超时设置 40s（大于后端 ADB 超时 30s），以保证结构化错误先返回。

#### POST /api/devices/{device_id}/disconnect

断开 TCP/IP 连接并从仓库删除设备记录。

- 路径参数：`device_id` — 形如 `"ip:port"`；实现按首个 `:` 拆分为 ip/port
- 响应 200：`{"success": true}`
- 注意：`device_id` 中不含 `:` 时（如 USB 序列号 `emulator-5554`），端点直接返回成功且不执行任何断开操作

#### POST /api/devices/{device_id}/install

在指定设备上安装 APK（同步等待 `adb install`）。

- 路径参数：`device_id`；查询参数：`apk_path`（主机侧 APK 路径，必填）
- 响应 200：`{"success": true, "result": "APK installed on <device_id>"}`
- 错误：400 `ADB_ERROR`（安装失败）

#### POST /api/devices/batch/install

在多个设备上安装同一个 APK（并发执行，信号量并发上限 5，单台失败不中止整体）。

- 请求体：设备 ID 的 JSON 数组（非对象），如 `["id1", "id2"]`
- 查询参数：`apk_path`（必填）
- 响应 200：`{"success": true, "results": ["id1: success", "id2: failed - <原因>"]}`

#### GET /api/devices/{device_id}/screenshot

- 路径参数：`device_id`
- 响应 200：PNG 二进制（`Content-Type: image/png`）

#### GET /api/devices/events（SSE）

设备上下线事件流（Server-Sent Events），事件源为后端每 5s 一次的设备状态刷新循环。

- 响应：`text/event-stream`，`Cache-Control: no-cache`、`Connection: keep-alive`、`X-Accel-Buffering: no`
- 事件格式（`data:` 单行 JSON）：

```
data: {"type": "connected", "device": {"id": "...", "model": "...", "os_version": "...", "resolution": [1080, 1920], "battery": 87, "status": "online"}}

data: {"type": "disconnected", "device_id": "..."}
```

客户端断开时后端移除回调；该流不发送初始快照事件（首次设备列表需另行调用 `GET /api/devices`）。

### 2.4 应用（`/api/apps`）

应用对象字段（`_app_to_dict`，`backend/app/interfaces/http/apps.py`）：
`package_name`、`version_name`、`version_code`、`install_time`、`update_time`、`apk_size_mb`、`is_system`、`is_running`、`pid`（未运行为 `null`）、`memory_kb`（不可用为 `null`）。

#### GET /api/apps/{device_id}

- 路径参数：`device_id`；查询参数：`include_system`（布尔，默认 `false`，仅第三方应用）
- 响应 200：

```json
{
  "apps": [ { "package_name": "com.example", "version_name": "1.0", "...": "..." } ],
  "total": 12,
  "running_count": 3,
  "total_memory_mb": 456.7
}
```

#### GET /api/apps/{device_id}/{package}

- 响应 200：单个应用对象
- 错误：404 `{"error": {"code": "HTTP_ERROR", "message": "App not found: <package>"}}`

#### GET /api/apps/{device_id}/{package}/memory

- 响应 200：`{"package": "<package>", "memory_kb": 12345}`
- 错误：404（内存信息不可用）

#### POST /api/apps/{device_id}/{package}/launch | /stop | /uninstall | /clear-data

四个动作端点参数、响应结构一致：

- 参数：仅路径参数
- 响应 200：`{"success": true, "package": "<package>"}`
- 错误：500 `HTTP_ERROR`，`message` 分别为 `Failed to launch app: <pkg>` / `Failed to stop app: <pkg>` / `Failed to uninstall app: <pkg>` / `Failed to clear app data: <pkg>`

### 2.5 调试（`/api/debug`）

#### POST /api/debug/sessions

创建调试会话。

- 查询参数：`device_id`（必填）、`user_id`（必填）
- 响应 200：`{"session_id": "<device_id>_<user_id>_<unix秒>"}`
- 副作用：写库并登记内存会话；logcat 采集不随创建启动（订阅者开启录制后才惰性启动）

#### GET /api/debug/sessions/{session_id}

- 响应 200：`{"session_id": "...", "device_id": "...", "user_id": "...", "is_active": true}`
- 错误：404 `SESSION_NOT_FOUND`（会话不存在）

#### DELETE /api/debug/sessions/{session_id}

关闭会话：取消 logcat 采集任务、关闭交互式 shell、向订阅者推送 `session_closed`、从内存移除（数据库记录保留）。

- 响应 200：`{"success": true}`（会话不存在时同样返回成功）

#### GET /api/debug/sessions/{session_id}/logs

查询日志：活跃会话读内存环形缓冲（容量 50000），已关闭会话回退数据库。

- 查询参数：`level`（可选，`V/D/I/W/E/F`，精确匹配）、`tag`（可选，子串匹配）、`limit`（可选，默认 `1000`，取缓冲尾部 N 条）
- 响应 200：`{"logs": [LogEntry, ...]}`
- LogEntry 字段：`ts`（float，Unix 秒）、`level`、`pid`、`tid`、`tag`、`message`、`raw`（原始 logcat 行）、`seq`（会话内单调递增序号，断线续传游标）

#### GET /api/debug/sessions/{session_id}/logs/export

导出日志为文件下载。

- 查询参数：`level`、`tag`（可选）、`format`（`json` 或 `csv`，默认 `json`；其他值按 `json` 处理）、`limit`（可选，默认 `50000`）
- 响应 200：附件下载（`Content-Disposition: attachment; filename="logs_<session_id>.json|csv"`），媒体类型 `application/json` 或 `text/csv`
- CSV 表头列：`timestamp, level, pid, tid, tag, message`

#### POST /api/debug/sessions/{session_id}/shell

在设备上执行 shell 命令（同步等待输出，同时写入 shell 历史）。

- 查询参数：`command`（必填）
- 响应 200：`{"output": "<stdout>"}`
- 错误：404 `SESSION_NOT_FOUND`（会话不存在）；ADB 执行失败 → 400 `ADB_ERROR`

#### DELETE /api/debug/sessions/{session_id}/logs

清空指定会话的所有日志。

- 响应 200：`{"deleted": 123}`（删除条数）

#### POST /api/debug/cleanup

手动触发一次日志清理：删除超过 `log_retention_days`（默认 7 天）的日志、超过 `shell_history_days`（默认 30 天）的 shell 历史；数据库超过 `max_db_size_mb` 时删除最旧日志。

- 响应 200：

```json
{
  "logs_deleted": 0,
  "shell_deleted": 0,
  "size_trimmed": 0,
  "db_size_bytes": 1234567,
  "db_size_mb": 1.18
}
```

#### GET /api/debug/stats

- 响应 200：`{"db_size_bytes": ..., "db_size_mb": ..., "active_sessions": 2, "total_subscribers": 1}`

### 2.6 性能（`/api/perf`）

性能指标对象字段（`PerformanceService._metrics_to_dict`）：
`ts`、`cpu_percent`、`total_memory_mb`、`used_memory_mb`、`fps`、`jank_count`、`current_activity`、`top_package`。

#### GET /api/perf/{device_id}/metrics

- 路径参数：`device_id`；查询参数：`limit`（默认 `100`，允许 1–3600）
- 响应 200：`{"metrics": [指标对象, ...], "count": 3}`（内存缓冲中最新的 limit 条，无数据时 `metrics` 为空数组）
- 错误：400 `HTTP_ERROR`，`message = "limit must be between 1 and 3600"`

#### POST /api/perf/{device_id}/start

启动采样（若已在运行则幂等）。

- 查询参数：`interval`（秒，默认 `1.0`，允许 0.1–60）
- 响应 200：`{"success": true, "device_id": "...", "interval": 1.0}`
- 错误：400 `HTTP_ERROR`，`message = "interval must be between 0.1 and 60"`

#### POST /api/perf/{device_id}/stop

- 响应 200：`{"success": true, "device_id": "..."}`

### 2.7 网络（`/api/network`）

后端为每台设备缓存一个采样器（5 分钟无访问自动清理），用于计算速率历史。

#### GET /api/network/{device_id}/stats

- 响应 200：

```json
{
  "ts": 1726045200.123,
  "rx_bytes": 12345678,
  "tx_bytes": 2345678,
  "rx_rate_kbps": 123.4,
  "tx_rate_kbps": 45.6,
  "active_connections": 12,
  "wifi_connected": true,
  "wifi_ssid": "MyWiFi"
}
```

`rx_bytes`/`tx_bytes` 为累计值；`wifi_ssid` 未连接时为 `null`。

#### GET /api/network/{device_id}/connections

- 查询参数：`protocol`（可选，`tcp` / `udp` / `tcp6`，为空返回全部）
- 响应 200：

```json
{
  "connections": [
    {
      "protocol": "tcp",
      "local_addr": "0.0.0.0",
      "local_port": 5555,
      "remote_addr": "192.168.1.2",
      "remote_port": 51234,
      "state": "ESTABLISHED",
      "uid": 1000
    }
  ],
  "total": 1
}
```

### 2.8 协作会话（`/api/sessions`）

内存态会话（未持久化，单进程部署适用）。会话对象结构：
`{"id", "device_id", "owner", "participants": {user_id: "admin"|"viewer"}, "active_controller"}`。
当前前端未调用本组端点。

#### POST /api/sessions

- 查询参数：`device_id`、`user_id`（均必填）
- 响应 200：`{"session_id": "<device_id>_<user_id>"}`（由 device_id 与 user_id 确定性派生；创建者同时是 owner、admin 与 active_controller）

#### GET /api/sessions/{session_id}

- 响应 200：完整会话对象
- 错误：404 `SESSION_NOT_FOUND`（会话不存在）

#### POST /api/sessions/{session_id}/join

- 查询参数：`user_id`（必填）、`permission`（可选，`admin` 或 `viewer`，默认 `viewer`）
- 响应 200：`{"success": true}`
- 错误：404 `SESSION_NOT_FOUND`（会话不存在）

#### POST /api/sessions/{session_id}/leave

- 查询参数：`user_id`（必填）
- 响应 200：`{"success": true}`；会话无剩余参与者时自动删除

#### POST /api/sessions/{session_id}/transfer

- 查询参数：`from_user`、`to_user`（均必填）
- 响应 200：`{"success": true}`
- 错误：403 `PERMISSION_DENIED`（`from_user` 不是 admin）、404 `SESSION_NOT_FOUND`（会话不存在）

## 三、WebSocket 端点

WS 端点全部在 `backend/app/main.py` 内联注册（`interfaces/ws/` 中的路由器未被 `include_router`，处理器函数由内联包装调用）。三个端点均无鉴权、无子协议协商，连接后立即生效。

### 3.1 WS /ws/video/{device_id} — 视频流与输入

处理函数：`backend/app/interfaces/ws/video.py:video_stream`。连接接受后对底层 TCP 设置 `TCP_NODELAY`（best-effort，失败不影响连接）。

连接建立过程：
1. 客户端直接连接 `ws://host:8765/ws/video/<device_id>`（无握手消息、无查询参数）
2. 服务端 `accept()` 并启动该设备的视频流（`StreamService.start_stream`），同时启动输入处理协程
3. 收到 SPS 与 PPS 后先下发 `config`，之后才开始转发视频帧（config 之前的帧被丢弃，不发送）
4. 客户端断开时停止该设备的视频流并释放编码器与 adb forward

#### 服务端 → 客户端

| 消息 | 载荷 | 说明 |
|------|------|------|
| `config`（JSON 文本） | `{"type": "config", "codec": "avc1.42E01E", "width": 1080, "height": 1920, "description": "<hex>"}` | `codec` 由 SPS 的 profile/constraint/level 动态提取；`description` 为 SPS+PPS 的 Annex B 字节（hex 编码，客户端转 AVCC 后创建 `VideoDecoder`）；自适应码率重启后会再次下发，客户端应重建解码器 |
| 视频帧（二进制） | H.264 Annex B Access Unit 字节串（含起始码） | 一条二进制消息 = 一个 AU（详见附录 A.4） |
| `restarting`（JSON 文本） | `{"type": "restarting", "bit_rate": 2000000}` | 编码器即将按新码率重启的预告（1–3s 黑屏属预期）；客户端应暂停回退 watchdog 并停止上报 stats。仅当客户端发送输入消息时检查并附带下发 |
| `error`（JSON 文本） | `{"type": "error", "message": "..."}` | 流处理异常时尽力发送，随后连接结束 |

#### 客户端 → 服务端

输入事件（JSON，按 `action` 分派，经编码器控制 socket 转发；控制 socket 不可用时回退 `adb shell input`）：

| action | 字段 | 语义 |
|--------|------|------|
| `touch` | `x`, `y`（整数，设备坐标） | 点击：后端发送 DOWN + UP |
| `swipe` | `x1`, `y1`, `x2`, `y2`, `duration`（毫秒） | 滑动：DOWN → 分段 MOVE（步长 5px，最多 100 步）→ UP；距离 <10px 或 `duration` <100ms 时退化为 DOWN+UP |
| `long_press` | `x`, `y`（整数，设备坐标）、`duration`（毫秒，默认 1000） | 长按：DOWN → 保持 `duration` → UP；adb 回退路径用同点 `input swipe x y x y duration` |
| `key` | `keycode`（Android keycode 整数） | 按键：DOWN + UP |
| `text` | `text`（字符串，UTF-8） | 文本注入 |

帧率上报（自适应码率决策输入，前端约每 2s 一次）：

```json
{ "op": "stats", "fps": 25 }
```

- `op == "stats"` 时读取 `fps`（float，非法值按 0 处理），喂给码率决策器；决策器触发档位切换时，服务端在后续输入消息处理后下发 `restarting`
- 未被识别的 `action` 走 `adb shell input` 回退路径

已知行为限制（见第四节）：同一设备同一时间只支持一条视频流；后加入的连接会收到空流并被关闭；任一端断开都会调用 `stop_stream`，可能中断其他观看者。

### 3.2 WS /ws/debug/{session_id} — 调试数据流

处理函数：`backend/app/interfaces/ws/debug.py:debug_stream`。连接后循环接收 JSON，按 `op` 字段分派；断开时自动退订并停止 shell 输出转发。

#### 客户端 → 服务端

| op | 载荷字段 | 说明 |
|----|---------|------|
| `subscribe` | `from_seq`（可选，整数） | 订阅实时日志 + shell 输出。新订阅者默认 `paused=true`（不接收日志，等待 `filter` 开启）。带 `from_seq` 时先补发断线期间的日志（见 `log_batch`）。订阅同时创建/复用交互式 shell（PTY），并直接发送其初始输出 |
| `filter` | `level`（可选）、`tag`（可选）、`paused`（布尔，默认 `false`） | 设置本订阅者的服务端过滤条件；`level` 精确匹配日志级别，`tag` 子串匹配；`paused=true` 暂停推送（暂停状态可让后端停止 logcat 采集） |
| `exec` | `command`（字符串） | 流式执行 shell 命令：逐行回 `shell_stream`（含 `done: false`），完成后回 `shell_output` |
| `input` | `data`（base64 字符串） | PTY 模式透传原始按键到设备 shell（如 `\r`→`\n` 由前端转换）；输出由后台转发器以 `shell_stream` 回推 |
| `unsubscribe` | — | 取消订阅并停止 shell 输出转发 |

日志导出不走本 WS（原 `export` 操作恒回未实现错误，2026-09-21 已移除）；用 HTTP 端 `GET /api/debug/sessions/{id}/logs/export`（json/csv 文件下载）。

#### 服务端 → 客户端

| type | 载荷 | 说明 |
|------|------|------|
| `subscribed` | `{"type": "subscribed", "session_id": "..."}` | 订阅完成确认 |
| `log_batch` | `{"type": "log_batch", "logs": [LogEntry...], "missing": 2}` | 仅当 `subscribe` 带 `from_seq` 时发送；`missing` 为已无法恢复的旧日志条数（超出缓冲窗口） |
| `log` | `{"type": "log", "entry": LogEntry}` | 实时日志条目（受该订阅者 `filter`/`paused` 约束） |
| `shell_stream` | `{"type": "shell_stream", "line": "..."}` 或 `{"type": "shell_stream", "line": "...", "done": false}` | shell 输出一行；`exec` 路径带 `done: false`，订阅初始输出与 PTY 转发路径不带 `done` |
| `shell_output` | `{"type": "shell_output", "output": "", "success": true, "done": true}` | `exec` 命令结束标记；异常时 `success: false` 且 `output` 为错误文本 |
| `filter_applied` | `{"type": "filter_applied", "level": ..., "tag": ..., "paused": ...}` | `filter` 应用确认，回显设置值 |
| `unsubscribed` | `{"type": "unsubscribed"}` | `unsubscribe` 确认 |
| `session_closed` | `{"type": "session_closed"}` | 会话被 `DELETE /api/debug/sessions/{id}` 关闭时主动推送 |
| `error` | `{"type": "error", "message": "..."}` | 操作失败（如 `input` 发送异常） |

消息顺序：`subscribe` 的响应依次为 `log_batch`（仅带 `from_seq` 时）→ 初始 `shell_stream`（设备 prompt，非空时）→ `subscribed`。

### 3.3 WS /ws/perf/{device_id} — 性能指标推送

处理函数：`backend/app/interfaces/ws/performance.py:stream_metrics`。

- 连接后自动启动该设备性能监控（若未启动，按默认 1s 间隔采样）
- 服务端 → 客户端：每秒一条纯指标 JSON 对象（**无 `type` 包装**，与其他 WS 端点不同）：

```json
{
  "ts": 1726045200.123,
  "cpu_percent": 23.5,
  "total_memory_mb": 4096,
  "used_memory_mb": 2048,
  "fps": 60,
  "jank_count": 0,
  "current_activity": "com.example/.MainActivity",
  "top_package": "com.example"
}
```

- 客户端 → 服务端：无协议（客户端发送的消息不被处理）；本端点也不接受 subscribe/filter 类消息
- 断开行为：仅移除推送队列，**不会**停止性能监控；监控需显式调用 `POST /api/perf/{device_id}/stop` 停止（或应用退出时清理）
- 内部异常时以 `close(code=1011, reason=<错误>)` 关闭连接

## 四、已知问题与现状说明

以下为核对源码时发现的契约/不一致项，**全部已处置**（2026-09-21）：

**已修复**：

1. `frontend/src/services/api.ts` 头注释 8000 → 8765。
2. `backend/app/main.py` 的 `__main__` 入口改为读取 `settings().server`（默认 8765，与 `run_server.py` 同源）。
3. CORS 默认来源 `http://localhost:5173` → `http://localhost:8080`（`config/settings.py`、`config/base.yaml`、`config/dev.yaml` 同步；`main.py` 注释同步修正）。
4. `backend/app/scrcpy/control_sender.py` 头注释 v2.4 → v4.1。
5. **错误约定统一**：`GET /api/devices/{id}`、`GET /api/debug/sessions/{id}`、`GET /api/sessions/{id}` 原“200 + `{"error": "<字符串>"}`”改为 404 + `{"error": {"code", "message"}}`；参数校验错误由 FastAPI 默认 `{"detail": [...]}` 改为 422 + `{"error": {"code": "VALIDATION_ERROR", "message": ...}}`。至此全站错误体统一为 `{"error": {...}}` 形状（见 1.4）。
6. **“未找到/无权限”状态码落地**：`exec_shell`/`join_session`/`transfer_control` 等服务层由抛裸 `ValueError`/`PermissionError` 改为 `SessionNotFoundError`（404）/`PermissionDeniedError`（403），不再被兜底处理器转成 500。
7. **WS `export` 移除**：`/ws/debug/{session_id}` 的 `export` 操作恒回未实现错误，属未落地残留，已从协议与代码中移除；日志导出由 HTTP 端 `GET .../logs/export` 承担（能力对称性以 HTTP 为准）。
8. **`long_press` 落地**：`ScrcpyEncoder.send_input` 与 `_fallback_adb_input` 均已支持 `long_press`（控制通道：DOWN → 保持 duration → UP；adb 回退：同点 `input swipe x y x y duration`）；前端 `inputController.ts` 长按改为直接发 `long_press`（不再用同点 swipe 模拟）。

**现状说明（非缺陷，如需变更属路线图级决策）**：

9. **协作会话与批量安装暂无前端调用**：`/api/sessions/*`（5 个端点）与 `POST /api/devices/batch/install` 后端已就绪且语义完整，前端 `api.ts` 无对应方法。二者对应 README 中「团队协作（多人控制）」路线图（当前为计划中状态）；批量安装无 UI 入口。启用时属新增前端功能，不属缺陷修复。
10. **视频 WS 单客户端假设（设计限制）**：`StreamService.start_stream` 对同一设备在流已活跃时直接返回（不产帧），第二条连接会拿到空流并被关闭；客户端断开即调用 `stop_stream`，多观看者场景下互相影响（该行为已在 3.1 节说明）。多人同时观看需上层做 fan-out（一路编码广播给多订阅者），属未实现的特性空间。

## 附录 A：视频流二进制协议（scrcpy-server v4.1，12 字节包头）

实现：`backend/app/scrcpy/stream_protocol.py`；默认启用（`stream.raw_stream_fallback=false`）。本协议运行在后端与 scrcpy-server 之间（经 `adb forward` 隧道），**不直接暴露给浏览器**——浏览器侧看到的是附录 A.4 的 AU 消息。

### A.1 建连握手

`tunnel_forward=true` + 默认元数据开关下，视频 socket 建连后依次：

| 偏移 | 长度 | 内容 |
|------|------|------|
| 0 | 1B | dummy byte（内容无意义） |
| 1 | 64B | 设备名（UTF-8，null 填充） |
| 65 | 4B | codec id（大端 uint32） |

codec id 语义：`0x68323634`（`"h264"`）正常；`0` 表示设备禁用该流（`StreamDisabled`，不中止镜像）；`1` 表示设备端配置错误（`StreamConfigError`，必须中止）；其他值中止。

### A.2 包类型

之后是连续的包，每包以 12 字节包头开始：

**session 包**（bit63 置位，即 `header[0] & 0x80`）：

| 字段 | 位置 | 说明 |
|------|------|------|
| flags | byte0 bit7 | 置位表示 session 包 |
| client_resized | byte3 bit0 | 客户端是否触发了 resize |
| width / height | offset 4/8，大端 uint32 | 流（重）起始宽高 |

出现在流起始与编码器重启（resize / 码率切换）时；后端据此更新分辨率并（首次）创建 `ControlSender`。

**媒体/配置包**：

| 字段 | 位置 | 说明 |
|------|------|------|
| PTS/flags | offset 0，大端 uint64 | bit62 = config 包；bit61 = 关键帧；低 61 位为 PTS |
| size | offset 8，大端 uint32 | 载荷长度 |
| payload | offset 12，`size` 字节 | config 包为 Annex B SPS/PPS（带起始码）；媒体包为 Annex B 编码帧 |

EOF 出现在包头或载荷中途视为设备断开，正常结束迭代并丢弃半包。

### A.3 raw_stream 兜底模式

配置 `stream.raw_stream_fallback=true` 时，服务端以 `send_frame_meta=false` + `raw_stream=true` 启动 scrcpy-server，输出为无包头的纯 Annex B 裸流，后端按 64KB 块读取并依赖启发式解析（此时分辨率回退 `adb shell wm size` 获取）。该模式为方案 17 实施项 1b 真机验证失败时的回退路径。

### A.4 浏览器侧帧格式（WebSocket 二进制消息）

后端将 H.264 NALU 聚合为 Access Unit（一帧）后发送，**一条 WS 二进制消息 = 一个完整 AU**，内容为 Annex B 字节串（NALU 含 3/4 字节起始码）。聚帧策略分两路：

- **包头协议模式**（默认）：每次从流中取出的是一个完整包（= 一个 AU），包内 NALU 直接拼接发送，包边界即帧边界。
- **兜底模式**：使用启发式聚帧（`backend/app/scrcpy/au_aggregator.py`）——SEI/SPS/PPS 为前缀 NALU，挂靠到后继第一个 VCL；IDR(5) 与非 IDR(1) 为新 AU 起点；AUD(9) 显式分界；输入尾部未闭合的 VCL AU 直接输出；无 VCL 可挂靠的悬空前缀跨批延后合并。

SPS 与 PPS 不随帧转发，仅用于构造 `config` 消息的 `description`（SPS+PPS 的 hex）。

## 附录 B：控制协议（后端 → scrcpy-server 控制 socket）

实现：`backend/app/scrcpy/control_sender.py`。后端在建立视频 socket 后另开第二条 TCP 连接到同一 `adb forward` 端口作为控制 socket，所有多字节字段为大端，消息通过 `asyncio.Lock` 串行化写入。

### B.1 消息类型

| type | 名称 | 总长度 | 结构（大端） |
|------|------|--------|-------------|
| 0 | INJECT_KEYCODE | 13B | `type(1) + action(1) + keycode(4) + repeat(4) + metaState(4)` |
| 1 | INJECT_TEXT | 5 + N | `type(1) + length(4) + text(UTF-8, N 字节)` |
| 2 | INJECT_TOUCH_EVENT | 32B | `type(1) + action(1) + pointer_id(8, 有符号) + x(4) + y(4) + screen_width(2) + screen_height(2) + pressure(2) + action_button(4) + buttons(4)` |
| 3 | INJECT_SCROLL_EVENT | 21B | `type(1) + x(4) + y(4) + screen_width(2) + screen_height(2) + hScroll(2, i16) + vScroll(2, i16) + buttons(4)` |
| 4 | BACK_OR_SCREEN_ON | 2B | `type(1) + action(1)` |
| 5 | EXPAND_NOTIFICATION_PANEL | 1B | `type(1)` |
| 6 | EXPAND_SETTINGS_PANEL | 1B | `type(1)` |
| 7 | COLLAPSE_PANELS | 1B | `type(1)` |
| 10 | SET_DISPLAY_POWER | 2B | `type(1) + on(1, bool)` |

`action` 取值：`0` = DOWN、`1` = UP、`2` = MOVE。触摸的 `pointer_id = -1` 表示虚拟鼠标；`pressure` 固定 `0xFFFF`（即 1.0）；`action_button`/`buttons` 固定 `1`（主键按下）。当前实现只使用了类型 0/1/2（类型 3–7、10 的方法已实现但 WS 层未暴露对应操作）。

### B.2 WS 输入动作到控制消息的映射

| WS 消息 | 控制消息序列 |
|---------|-------------|
| `{"action": "touch", "x": X, "y": Y}` | TOUCH(DOWN, X, Y) → TOUCH(UP, X, Y) |
| `{"action": "swipe", "x1": .., "y1": .., "x2": .., "y2": .., "duration": D}` | TOUCH(DOWN, x1, y1) → N × TOUCH(MOVE, 插值点) → TOUCH(UP, x2, y2)；步长 5px、最多 100 步；距离 <10px 或 D <100ms 时仅 DOWN+UP |
| `{"action": "long_press", "x": X, "y": Y, "duration": D}` | TOUCH(DOWN, X, Y) → 保持 D 毫秒 → TOUCH(UP, X, Y)；D 默认 1000 |
| `{"action": "key", "keycode": K}` | KEYCODE(DOWN, K) → KEYCODE(UP, K) |
| `{"action": "text", "text": T}` | TEXT(T) |

控制通道不可用（控制 socket 未建立或分辨率未知）或发送异常时，自动回退 `adb shell input tap/swipe/keyevent/text`（`ScrcpyEncoder._fallback_adb_input`）；`long_press` 的回退命令为同点 `input swipe X Y X Y D`。

坐标系：控制消息使用设备物理坐标。后端启动 scrcpy-server 时强制 `max_size=0`（不缩放），保证视频帧尺寸等于设备物理分辨率，使前端 canvas 坐标可直接作为设备坐标使用。

### B.3 scrcpy-server 启动参数（协议相关）

由 `ScrcpyEncoder.start` 构造，固定值含：`tunnel_forward=true`、`control=true`、`audio=false`、`show_touches=false`、`stay_awake=false`、`power_off_on_close=false`、`clipboard_autosync=false`、`video_codec=h264`，以及 `max_size`（强制 0）、`max_fps`、`video_bit_rate`（由 `stream.*` 配置换算为 bps）。socket 名固定 `scrcpy`，本地转发端口 `27183 + hash(device_id) % 100`。
