# 方案 32：PTS 全链透传（P2-4）

- 日期：2026-09-30
- 状态：**实施完成（2026-10-08），真机验收闭环**——后端段 55b4457 / 前端段 69c5fc3 / 脚本段 ed7b852 + 验收期修复
- 关联：[docs/性能对标与优化清单.md](../docs/性能对标与优化清单.md)（P2 第 4 项）、
  `backend/app/scrcpy/stream_protocol.py`、`backend/app/infrastructure/stream/scrcpy.py`、
  `backend/app/interfaces/ws/video.py`、`frontend/src/services/h264VideoStream.ts`

## 一、问题

P2 清单原文：**PTS 全链丢弃（后端不转发、前端置 0）→ 无法基于时间戳
做延迟量化与丢帧决策，也是 #4 实测的技术前提。**

两个「前置理由」截至本调研（2026-09-30）的现状：

| 理由 | 现状 |
|---|---|
| #4 延迟量化技术前提 | **已用物理拍照法实测闭环**（§4.2：中位 76ms、65–83ms 5 样本）——PTS 已不是阻塞项 |
| 丢帧决策 | **已由方案 31（§4.9）闭环**——追赶状态机基于解码队列水位 + IDR 锁定，不依赖 PTS |

因此本项的剩余价值需重新定界（见 §八）：**数据链路补全 + WebCodecs
timestamp 语义正确化**，而非用户可感知的体验修复。

## 二、现状走查（2026-09-30 核实）

PTS 数据全链的四个断点（设备端 → 浏览器）：

| 层 | 现状 | 断点 |
|---|---|---|
| 设备端（scrcpy-server v4.1） | 媒体包 12B 头 = 8B PTS/flags（bit62=config、bit61=key、其余 61 位 PTS，**µs，设备单调时钟**）+ 4B 长度 | — |
| `stream_protocol.py` | `read_packets` 已解析出 `MediaEvent.pts`（`parse_frame_header`） | —（已解析） |
| `infrastructure/stream/scrcpy.py` | `read_socket` 队列只放 `event.payload`（bytes），pts 丢弃（:422/:440/:455） | **断点 1** |
| `stream_service.py` | `start_stream` 产出 `AsyncIterator[bytes]`（无 PTS 位） | **断点 2** |
| `interfaces/ws/video.py` | `send_bytes(chunk)` 发纯 Annex B AU（12B 头已剥） | **断点 3** |
| 前端 `h264VideoStream.ts:474` | `EncodedVideoChunk({ timestamp: 0 })`「服务端未提供 PTS」 | **断点 4** |

其他事实：

- **消费者面**：`start_stream` 仅 video.py 一个消费者；直连
  `encoder.start` 的脚本 2 个（`spike_backpressure.py` /
  `spike_copy_chain.py`），订阅 `/ws/video` 的脚本 7 个
  （`capture_ws_frames.py` / `spike_bitrate_restart.py` /
  `spike_screenshot_feedback.py` / `spike_stats_reporter.py` /
  `spike_still_frame_signature.py` / `spike_stream_reuse.py` /
  `verify_recovery_19.py`）；
- **raw 兜底模式**（`stream.raw_stream_fallback`，默认 False 且从未启用）
  为裸流块读，无 PTS 可言；
- WebCodecs `EncodedVideoChunk.timestamp` 单位亦为 **µs**——设备 PTS
  可直接喂入，无需换算。

## 三、设计

### 3.1 线上格式（WS 二进制帧）

```
[8B PTS（大端无符号）][Annex B AU 载荷]        ← 新
[Annex B AU 载荷]                              ← 旧
```

- 单消息不变（不增加消息数，方案 17/26 的消息数优化不回退）；
- 固定 8B 前缀，PTS 原值透传（µs，无换算）；
- is_key 前端继续自扫（`(data[off]&0x1f)===5`），不在前缀中重复
  （YAGNI：前缀只放「前端自身无法获得」的 PTS）；
- config 包路径（SPS/PPS 拦截后 VCL 合并发送）PTS 按原值传
  （config 包 PTS 域通常为 0，无害）；raw 兜底模式 pts=0
  （保持旧行为，该路径从未启用）。

### 3.2 后端改动（三处）

| 文件 | 改动 |
|---|---|
| `infrastructure/stream/scrcpy.py` | `_data_queue` 元素 `bytes` → `tuple[int, bytes]`（pts, payload）；raw 分支入队 `(0, chunk)`；`yield` 元组 |
| `application/stream_service.py` | `start_stream` 签名 `AsyncIterator[tuple[int, bytes]]`（纯透传，逻辑零变化） |
| `interfaces/ws/video.py` | 解包 `(pts, payload)`；发送处组装 `struct.pack('>Q', pts) + payload`（每帧一次拼接，~KB 级 memcpy，成本可忽略；方案 26 的零拷贝仍成立于队列内） |

### 3.3 前端改动

`h264VideoStream.ts handleBinaryFrame`：

```ts
const pts = Number(new DataView(data.buffer, data.byteOffset, 8).getBigUint64(0))
const payload = data.subarray(8)
// scanFrame(payload)（原逻辑）；EncodedVideoChunk({ timestamp: pts })
```

- `timestamp: pts` 替换写死的 0——`VideoFrame.timestamp` 继承正确值；
- PTS < 2^53（µs 计时 285 年）转 Number 安全；
- **不做**观测功能（延迟漂移面板、PTS 丢帧间隙统计等）——本期只补
  数据链路，消费方按需另立（YAGNI）。

### 3.4 兼容与波及

- 前后端同仓发布（exe 一体包），无外部消费者；旧前端 + 新后端会
  解包失败（前置 8B 当帧数据）——属于同一发版内的原子变更；
- 9 个 spike/verify 脚本同步小改（encoder 直连 2 个解包元组；WS 侧
  7 个剥离 8B 前缀后再统计）；
- e2e 用例实施时核查（`frame-drop-31` 的 WS 代理只转发不受影响；
  若有伪造二进制帧的 mock 用例需加前缀）。

## 四、边界与风险

| 风险 | 缓解 |
|---|---|
| WS 格式破坏性变更 | 同仓原子发布；发版检查清单已有前后端版本号同步项（E025 教训） |
| 每帧 8B 前缀拼接拷贝 | ~7KB/帧 @30fps ≈ 210KB/s memcpy，可忽略；不破坏方案 26 的队列零拷贝 |
| 设备/编码器重启后 PTS 跳变（不连续） | 透传层不做任何假设（纯搬运）；未来消费方须容忍跳变；前端 timestamp 非单调不影响无 B 帧流解码（现 0 常量本就非单调） |
| 「跨时钟」误用 | 设备时钟与浏览器/后端时钟不同源：**绝对端到端延迟不可由 PTS 直接得**（拍照法为准）；PTS 支持的是帧间隔/抖动/丢帧间隙等**相对分析**——方案文档如实定界，勿过度承诺 |
| 收益不足 | §八 给出「不做」选项——若用户判定价值不匹配成本，本项可直接关闭 |

## 五、测试方向（TDD）

`tests/` 与前端 vitest 同步（FakeVideoDecoder / FakeWs 基建已有）：

1. **后端队列携带 PTS**：feed 12B 头 + 载荷字节 → `read_socket` 产出
   `(pts, payload)`（协议模式）；raw 模式产出 `(0, chunk)`；
2. **video.py 组装**：WS 输出消息前 8B == `struct.pack('>Q', pts)`、
   其余为原载荷（含 config 拦截路径的 VCL 合并分支）；
3. **前端解包**：FakeWs 发 `[8B PTS][payload]` → `EncodedVideoChunk.timestamp
   == pts`、`scanFrame` 收到的是剥离后的载荷（FakeVideoDecoder 捕获 init）；
4. **不回归**：既有单测全绿（h264VideoStream 测试的消息构造统一加
   8B 前缀；video.py/stream_service 既有用例同步元组化）；门禁
   pytest + ruff + mypy strict、vitest + vue-tsc + eslint。

## 六、真机验收方向

| 步骤 | 操作 | 期望 |
|---|---|---|
| 1 | .18 活动流 spike（`capture_ws_frames.py` 更新后） | 收到 PTS 单调递增（编码器重启处跳变除外）、相邻帧 PTS 间隔中位 ≈ 33ms（与 30fps 一致）、逐帧与帧计数比对无错位 |
| 2 | e2e 回归（session 全链） | 页面正常出帧（video-stream 用例），方案 31 用例不受影响 |
| 3 | 前端 timestamp 正确性 | 页面内探针（e2e 注入 hook 捕获 EncodedVideoChunk 或用 stats 输出）验证 timestamp == 设备 PTS（抽帧比对后端日志） |

（步骤 3 探针形式实施时定；若 hook EncodedVideoChunk 不可行，
以单测 + 后端日志抽帧比对为准。）

### 验收结果（2026-10-08，.18 时钟页活动流）

| 步骤 | 结果 | 证据 |
|---|---|---|
| 1 WS 抓包 | ✅ | `capture_ws_frames.py`：778 帧 PTS **非递增 0 次**；相邻间隔中位 **34.6ms**（≈30fps）；起始码非法包 0 / 每帧恰 1 NALU / 逐帧无错位；config 1 次、重复载荷 0 |
| 2 e2e 回归 | ✅ | video-stream **2 passed**（帧计数增长 + canvas 真实渲染）；frame-drop-31 场景 1+2 **1 passed**（恢复 29.9fps、催帧恰一次、warn 0）+ 场景 3 **1 passed**（丢帧 0 / 催帧 0 / 无误触发）；stream-stability-19 **3 passed**（含卡死注入回切链） |
| 3 前端 timestamp | ✅ | 临时探针（e2e addInitScript 包装 `VideoDecoder` 捕获 `chunk.timestamp`，验证后即删）：**155/155 严格递增**、中位间隔 **34.63ms**——与 WS 侧 34.6ms 逐值吻合，端到端闭环 |

验收期修复：`capture_ws_frames.py` 中文 Windows 下 GBK stdout 无法输出 µ 字符
（UnicodeEncodeError）→ 按项目既有的 `sys.stdout.reconfigure(encoding="utf-8")`
模式修复。门禁：后端 pytest **804 passed + 1 skipped** / ruff / mypy strict 63 文件；
前端 vitest **422 passed**（前端零改动）。

## 七、实施范围

- **涉及文件**：`infrastructure/stream/scrcpy.py`、`application/stream_service.py`、
  `interfaces/ws/video.py`、`frontend/src/services/h264VideoStream.ts`、
  相关单测（后端 tests/、前端 *.test.ts）、9 个 tests/manual 脚本同步；
- **后端协议零新增**（复用 `stream_protocol.py` 已有解析结果）、
  设备端零改动；
- **不做**：观测功能（漂移面板/PTS 统计）、PTS 驱动的丢帧决策（方案 31
  已闭环）、跨时钟延迟换算（拍照法为准）。

## 八、判定与决策点

调研揭示了本项的**价值再定界**（如实呈现，供立项决策）：

- **不是**「修复缺陷」：清单原文的两个前置理由——#4 延迟量化的
  「技术前提」已由物理拍照法实测（76ms）达成；丢帧决策已由方案 31
  闭环。PTS 透传不改变任何现有用户体验指标；
- **是**「数据链路补全」：消除唯一的视频元数据丢失点（PTS），前端
  `EncodedVideoChunk.timestamp` 从假值 0 变为真值（WebCodecs 语义
  正确、`VideoFrame.timestamp` 继承正确）；为未来观测/分析能力
  （帧间隔抖动、丢帧间隙、接收侧漂移）提供数据基础。

两个选项：

| 选项 | 内容 |
|---|---|
| A（推荐） | 按本方案完整实施——成本可控（4 层小改 + 9 脚本同步 + 单测/e2e 回归），关闭 P2 最后一项 |
| B | 直接关闭 P2-4——「拍照法已达成量化目标、丢帧决策已闭环，无直接收益」；在性能清单如实记录关闭理由 |

（另可裁剪出「仅后端透传到 WS、前端暂不消费」的中间态，但前端置 0
与消费方缺位使该中间态无独立价值，不单列。）