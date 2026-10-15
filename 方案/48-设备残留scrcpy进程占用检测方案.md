# 方案 48：起流前设备残留 scrcpy 进程占用检测

> **状态**：**立项，调研完成，待 review 拍板**（用户 2026-10-15 指示：
> 「占用检测方案：起流前 ps 扫残留 scrcpy 进程，既能提示"他人会话"也能提示
> "自己上次异常残留"」）。
> **约束**：零新依赖；master 直做；门禁全量口径（ruff + mypy strict +
> pytest cov≥80 + vitest / vue-tsc + `npx eslint .` 全量）；存量 API 只增不改；
> 前端展示复用既有 `extractApiError` 回显通道（目标零前端改动）。

## 一、背景与目标

1. **痛点**：设备端 `scrcpy.Server` 进程残留时（上次会话异常退出：后端崩溃、
   电脑断电强杀、进程未随本地 adb 子进程退出），再次起流会撞上
   `localabstract:scrcpy` socket 被占——新 server bind 失败提前退出，或本端
   连到旧会话（参数错配、画面异常）。现状报错为底层英文
   `RuntimeError("scrcpy-server exited early: ...")`，用户无法理解也无法行动。
2. **目标**：起流**前**检测设备端残留 scrcpy 进程，命中时给出明确中文提示，
   区分/覆盖两类来源：「其他工具或客户端的会话」与「本软件上次异常残留」，
   让用户知道该关谁、怎么办。

## 二、实证调研（2026-10-15，.25 rk3288 box 活体取证）

### 2.1 检测原语可行性（真机实测）

- **`ps | grep scrcpy` 路线不可行**：旧 toybox `ps` 不支持 `-A`
  （`bad pid '-A'`）；裸 `ps` 的 NAME 列只显示 `app_process`，**无 "scrcpy"
  字样**，grep 命中 0（实测）。
- **可行原语**：读设备侧 `/proc/<pid>/cmdline`：

  ```bash
  adb -s <dev> shell "grep -lE 'scrcpy[.]Server' /proc/[0-9]*/cmdline 2>/dev/null"
  ```

  实测输出（.25 现场即有残留）：
  - `/proc/2256/cmdline`（启动残留的 sh 包装进程，cmdline 含完整启动命令）
  - `/proc/2258/cmdline`：`app_process / com.genymobile.scrcpy.Server 5.0.1
    log_level=info max_size=0 max_fps=30 video_bit_rate=4000000
    video_codec=h264 tunnel_forward=true control=true audio=false ...`
    ——**与本软件启动参数指纹完全一致**，即本后端上次会话残留（活体证据）。
  - `/proc/2268/cmdline`：`com.genymobile.scrcpy.CleanUp`（Server 的伴生监控
    进程，模式不匹配，仅经父链出现——正则 `scrcpy[.]Server` 不命中 ✓）。
- **自匹配规避**：正则写成 `[.]` 字符类（`scrcpy[.]Server`），grep 与 sh
  包装进程自身 cmdline 里的**字面 `[.]`** 不匹配正则的"点后接 Server"；
  但**残留启动命令的 sh 父进程**（如 2256）cmdline 含真实的
  `com.genymobile.scrcpy.Server` 字样，会一并命中——无害（判据只取
  "输出非空"；计数会话时按 PID 去重即可）。`/proc/[0-9]*` 通配天然排除
  `self`/`thread-self` 伪目录。
- **误报检查**：本系统另一常驻 `app_process`（rotationwatcher.jar，PID 15071）
  cmdline 不含 `scrcpy.Server`，**不误报** ✓（实测命中列表无它）。
- **权限**：shell 用户可读同用户进程的 cmdline ✓。glob 由设备侧 sh 展开
  （toybox sh 支持）✓。单次开销 ≈ 一次 adb 往返（百 ms 级）。

### 2.2 残留根因（代码现状）

- `stop()`（`infrastructure/stream/scrcpy.py:829`）terminate/kill 的是**本地
  adb shell 子进程**；设备侧 `app_process` 不保证随之退出（无 PTY、信号不
  传播；server 阻塞在读 socket 时依赖 TCP 断开自退，非确定性）。异常路径
  （崩溃、断电）同理。故残留会跨会话、跨重启累积。
- 起流链路（同文件 `start()`）：push JAR → `forward --remove` → `forward` →
  spawn server → 等 stderr `Device:`（264-296）→ 早退检查（336）→
  连 socket（348）。残留场景在 spawn 后失败：新 Server bind 抽象套接字冲突
  早退 → 报 `scrcpy-server exited early`（英文、不可行动）；或本端连到旧
  Server（旧会话 backlog）→ 参数错配流异常。**均无占用语义**。
- 归属信息可用面：`stream_service.active_streams`（device_id → token）
  知晓本后端**当前**是否有该设备活跃会话；残留 cmdline 全文含启动参数，
  可与本软件参数指纹比对（`max_size=0 ... tunnel_forward=true audio=false
  clipboard_autosync=false` 特征串）近似判定"本软件上次会话"。

## 三、方案设计

### 3.1 检测点与流程（T1）

- 位置：`ScrcpyStream.start()` 最前（push JAR 之前，早失败早止损），新增
  `_detect_stray_server(device_id) -> bool`：执行 §2.1 原语（复用本文件既有
  `create_subprocess_exec + CREATE_NO_WINDOW` 直调风格），输出非空即命中。
- 跳过条件：本后端对该设备已有活跃会话时不应进入重复 start（编排层
  `active_streams` 已拦）；防御性跳过检测避免自报占用。
- 命中行为：抛新异常 `ScrcpyOccupiedError(OpenScrcpyException)`
  （`core/exceptions.py`，与 `DeviceNotReadyError` 同模式）：HTTP 400 +
  `code=SCRCPY_SESSION_OCCUPIED` + 中文 hint。scan/WS 起流路径的既有
  `except Exception` 编排自动归类失败并透传 message；前端
  `extractApiError` 原样回显，**零前端改动**（方案 47 假成功守卫同型）。

### 3.2 提示文案（含两类归因，T1）

hint 模板（`.format(device=...)`）：

- 主模板：`设备 {device} 上检测到已在运行的 scrcpy 会话残留。可能是：
  ①本软件上次会话异常退出未清理干净；②其他工具（scrcpy 桌面版/其他客户端）
  正在占用该设备。请关闭其他占用程序后重试`；
- 增强归因（推荐纳入，成本低——cmdline 全文本就在检测输出中）：命中进程
  参数指纹与本软件启动串一致 → 追加/替换为「判定为本软件上次会话的残留，
  重启本软件设备端会话或联系管理员清理」；不一致 → 「疑似其他工具的会话」。
  归因仅作提示措辞差异，不做保证。

### 3.3 拍板点（需用户决策）

| # | 问题 | 选项与建议 |
|---|------|-----------|
| D1 | 检测到占用后的动作 | **A. 仅提示阻断**（推荐：改动最小、无误伤）；B. 提示 + 「一键清理残留」按钮（需新端点 `POST /devices/{id}/scrcpy-cleanup` + 设备侧 `kill <PID>` + 前端弹窗，中改动）；C. 自动清理并继续起流（否决：可能误杀他人进行中会话）。 |
| D2 | 是否顺带治理残留根因 | 48b 可选扩展：起流时记录设备侧 Server PID，`stop()` 追加设备侧 `kill`，防新增残留。不做则本方案只管"检测出历史残留"。建议：**本期只做检测（48a）**，48b 视 D1 结果另议。 |
| D3 | 归因增强是否纳入 | 纳入（推荐，§3.2）或不区分统一文案。 |

## 四、测试与验证

- 单测（`tests/infrastructure/`）：mock adb 子进程——命中/未命中/异常输出
  解析；hint 模板与 code 断言；异常经 scan 编排归类 connect_failed/stream
  失败路径。
- 集成：HTTP 端点 400 响应体结构（仿 `test_http_devices.py` 的
  `test_connect_device_not_ready_passes_400`）。
- 真机手工：起流中途拔网/强杀后端造残留 → 再起流应见中文占用提示；
  .25 现存残留（取证时 2258）可直接作阳性样本；清理后阴性复测。
  .18/.33 补测 grep 原语可用性（实施期）。

## 五、风险与边界

- 起流耗时 +1 次 adb 往返（≈百 ms，相对推流建立占比小）。
- 极老 ROM 无 toybox grep → 检测子进程失败：降级为**放行 + debug 日志**
  （不因检测能力缺失阻断正常起流）。
- 本检测面向"设备端残留"；本机多实例后端并发起流（单开模式方案 23 已约束）
  不在范围。

## 六、任务分解（拍板后）

- **T1** 检测原语 + 异常类 + start() 接入 + hint 模板（含 D3 归因）+
  单测/集成测试 + CHANGELOG。
- **T2**（若 D1=B）cleanup 端点 + AdbDriver kill 原语 + 前端确认弹窗。
- **回写**：进度追踪、路线图第 16 项登记、本方案状态行。

## 更新记录

- 2026-10-15：立项。完成 .25 真机取证（ps 局限、/proc cmdline 原语、
  活体残留样本与参数指纹、rotationwatcher 不误报）与起流/停止代码调研；
  设计方案与拍板点 D1-D3，待 review。
