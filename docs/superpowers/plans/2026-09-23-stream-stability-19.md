# 方案 19「推流连接稳定性优化」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用后端空闲保活（RESET_VIDEO）+ 卡死探针自愈 + 前端静止感知/关键帧请求/状态治理/WS 重连，消灭静止设备上的「正在连接...」误判循环。

**Architecture:** 后端 `ScrcpyEncoder` 在 yield 循环实现保活与探针（1a/1b），卡死判定上抛 `EncoderStalledError` 由 `StreamService` 防风暴重启（epoch+1 复用既有 WS 重置机制）；前端从 config 消息读取 `idle_reset_seconds` 联动回退阈值，resume 前请求关键帧，「正在连接...」仅保留给首次连接，WS 层补指数退避重连。

**Tech Stack:** Python 3 / FastAPI / asyncio（后端）；Vue 3 / TypeScript / vitest（前端）；scrcpy v4.1 控制协议 type=17 RESET_VIDEO。

**Spec:** [方案/19-推流连接稳定性优化.md](../../../方案/19-推流连接稳定性优化.md)（实测数据与裁定以该文档为准）

## Global Constraints

- 测试文件只放 `tests/`（后端）或与源文件同目录的 `*.test.ts`（前端既有约定）；禁止根目录/backend 下建测试。
- 后端门禁：`PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80`、`ruff check backend/app`、`mypy backend/app`（strict 清零）。
- 前端门禁：`cd frontend && npm run lint`、`npx vitest run --coverage`、`npm run build`。
- 每次任务提交：中文提交信息 + 结尾空行 + `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`。**不推送远端**。
- 严格四层架构：`interfaces/ws/video.py` 不得直接 import `config.settings`，配置经 `StreamService` 方法透出。
- 浏览器仅支持 Chrome（最新两个稳定版），可用 WebCodecs。
- Git Bash 执行 ADB shell 需 `MSYS_NO_PATHCONV=1`。
- 真机验证仅用 `192.168.8.18:5555` / `192.168.8.33:5555` / `192.168.8.25:5555`；.25 探测优先独立 scid 脚本，避免影响用户现场会话。
- 明确不做（方案 2.6 节）：不换软编、不加 i-frame-interval、不降分辨率档位、不改定制 jar、**不加 1Hz WS keepalive 心跳**（后端编码器保活已承担判活职责，YAGNI）。

## 已定裁定（执行时不再讨论）

1. **2b「静止不切截图」无独立代码**：后端保活让静止时仍有周期性 IDR 到达 + 前端阈值随 `idle_reset_seconds` 抬升（Task 3），两者叠加自然成立，不再单写分支。
2. **阈值联动公式**：`STALL=max(3000, 2k)`、`NO_STREAM=max(8000, STALL+2000)`、`HARD=max(15000, NO_STREAM+5000)`（k=保活间隔 ms；5s → 10/12/17s，余量覆盖 2s 轮询粒度 + 0.4s IDR 延迟）。
3. **防风暴**：60s 滑动窗口内卡死重启 ≤3 次，超限把 `EncoderStalledError` 抛给 WS 层走既有 error → 回退链；退避 sleep 依次为 1s、2s（首次不 sleep）。
4. **保活 tick**：yield 循环 `wait_for` 超时改为 `min(2.0, idle_reset/2)`，兼顾响应精度与测试可加速。
5. **无 `_control_sender` 时**（控制 socket 未就绪）跳过保活与探针，防误判卡死。
6. **探针竞态**：收到任何数据即将 `reset_sent_at` 复位，RESET 与真实帧竞态不会误报。

---

### Task 1: RESET_VIDEO 协议支持 + 后端空闲保活（1a）+ config 透传与重发

**Files:**
- Modify: `backend/app/scrcpy/control_sender.py`（TYPE 常量区 27-35 行、`ControlSender` 类内）
- Modify: `config/settings.py:86-97`（`StreamConfig`）
- Modify: `config/base.yaml:25-29`（stream 节）
- Modify: `backend/app/infrastructure/stream/scrcpy.py:435-454`（yield 循环；文件头补 `import time`）
- Modify: `backend/app/application/stream_service.py`（新增 `idle_reset_seconds()`）
- Modify: `backend/app/interfaces/ws/video.py:151-165`（SPS/PPS 变化重发）、`:273-279`（config 加字段）
- Test: `tests/scrcpy/test_scrcpy_encoder.py`、`tests/unit/test_metrics_config.py`、`tests/unit/test_video.py`

**Interfaces:**
- Consumes: 既有 `ControlSender._send`（内部已带 `asyncio.Lock` + drain 超时）
- Produces: `ControlSender.reset_video() -> None`；`StreamConfig.idle_reset_seconds: float`（默认 5.0，0=关闭）；`StreamService.idle_reset_seconds() -> float`；config 消息新增 `idle_reset_seconds` 字段——Task 2/3/4 依赖。

- [ ] **Step 1: 写失败测试（control_sender）**

在 `tests/scrcpy/test_scrcpy_encoder.py` 末尾追加（复用文件内既有 `FakeWriter`）：

```python
async def test_reset_video_sends_type17():
    from app.scrcpy.control_sender import ControlSender
    writer = FakeWriter()
    sender = ControlSender(writer, (1360, 768))
    await sender.reset_video()
    assert bytes(writer.written) == bytes([17])
```

- [ ] **Step 2: 写失败测试（配置默认值与 yaml 同步）**

在 `tests/unit/test_metrics_config.py` 追加（沿用该文件读 yaml 的既有方式；若无 helper 则如下直读）：

```python
def test_stream_idle_reset_seconds_default():
    from config.settings import StreamConfig
    assert StreamConfig().idle_reset_seconds == 5.0


def test_base_yaml_stream_idle_reset():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config/base.yaml").read_text(encoding="utf-8"))
    assert cfg["stream"]["idle_reset_seconds"] == 5.0
```

- [ ] **Step 3: 写失败测试（保活循环）**

在 `tests/scrcpy/test_scrcpy_encoder.py` 追加。装配沿用该文件既有 `ConnectionHarness` / `make_handshake` / `make_frame_header` 构造「握手 + 1 帧后视频流静默、control writer 为 `FakeWriter`」场景：

```python
async def test_idle_keepalive_sends_reset_video(monkeypatch):
    """静止超过 idle_reset_seconds 后经控制 socket 发出 type=17，收到帧后计时复位。"""
    import types
    fake = types.SimpleNamespace(stream=types.SimpleNamespace(
        idle_reset_seconds=0.2, raw_stream_fallback=False))
    monkeypatch.setattr("app.infrastructure.stream.scrcpy.settings", lambda: fake)
    conn = ConnectionHarness(...)   # 既有 API：1 帧后永久静默，control=FakeWriter
    frames = [f async for f in conn.take_first_frame()]  # 启动并消费首帧（按实际 API 适配）
    assert frames
    await conn.wait_until(
        lambda: bytes(conn.control_writer.written) == b"\x11", timeout=1.5)
```

落地时以 harness 实际方法名组装等语义三步：①启动并消费 1 帧；②静默等待；③断言 `FakeWriter.written` 恰为一个 `0x11` 字节。禁止放宽成「只要发过 RESET 就算过」——必须验证**首帧后无数据**才触发（0.2s 阈值下 1.5s 内必达）。

- [ ] **Step 4: 运行确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/scrcpy/test_scrcpy_encoder.py tests/unit/test_metrics_config.py -q`
Expected: FAIL（`AttributeError: reset_video` / `idle_reset_seconds` 不存在）

- [ ] **Step 5: 实现 control_sender**

`control_sender.py` 常量区 `TYPE_SET_DISPLAY_POWER = 10` 之后加：

```python
TYPE_RESET_VIDEO = 17
```

`ControlSender` 类内加方法（放在 `update_resolution` 之后）：

```python
    async def reset_video(self) -> None:
        """发送 RESET_VIDEO(type=17)：服务端复位视频管线，0.1-0.4s 内产出
        新 IDR 并重发 SESSION/CONFIG 包（方案 19 实测）。"""
        await self._send(struct.pack(">B", TYPE_RESET_VIDEO))
```

- [ ] **Step 6: 实现配置项**

`config/settings.py` `StreamConfig` 末尾（`raw_stream_fallback` 之后）加：

```python
    # 空闲保活（方案 19 实施项 1a）：静止无帧超过 N 秒经控制 socket 发 RESET_VIDEO；0=关闭
    idle_reset_seconds: float = 5.0
```

`config/base.yaml` stream 节加：

```yaml
  idle_reset_seconds: 5.0
```

- [ ] **Step 7: 实现 ScrcpyEncoder 保活**

`scrcpy.py` 文件头 import 区补 `import time`。将 435-452 行 yield 循环替换为：

```python
        idle_reset = float(settings().stream.idle_reset_seconds)
        tick = min(2.0, idle_reset / 2) if idle_reset > 0 else 2.0
        last_data_at = time.monotonic()
        reset_sent_at: float | None = None
        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(
                        self._data_queue.get(), timeout=tick
                    )
                    if data is None:
                        logger.info("queue_sentinel_received", device=device_id)
                        break
                    last_data_at = time.monotonic()
                    reset_sent_at = None
                    yield data
                except asyncio.TimeoutError:
                    if self.process and self.process.returncode is not None:
                        logger.info("server_process_ended",
                                    device=device_id,
                                    returncode=self.process.returncode)
                        break
                    if idle_reset <= 0 or self._control_sender is None:
                        continue
                    now = time.monotonic()
                    if reset_sent_at is None:
                        if now - last_data_at >= idle_reset:
                            reset_sent_at = now
                            try:
                                await self._control_sender.reset_video()
                                logger.info("idle_reset_video_sent",
                                            device=device_id,
                                            idle_s=round(now - last_data_at, 1))
                            except Exception as e:
                                logger.warning("idle_reset_video_failed",
                                               device=device_id, error=str(e))
                    continue
        except Exception as e:
            logger.error("stream_error", device=device_id, error=str(e))
```

- [ ] **Step 8: 实现 StreamService 透出 + video.py config**

`stream_service.py` 在 `use_packet_protocol` 之后加：

```python
    def idle_reset_seconds(self) -> float:
        """当前空闲保活间隔（秒），0=关闭；随 config 消息下发给前端联动回退阈值。"""
        return float(settings().stream.idle_reset_seconds)
```

`video.py` `_send_config` 的 `send_json` 字典加一行：

```python
        "idle_reset_seconds": stream_service.idle_reset_seconds(),
```

`video.py` SPS/PPS 分支（151-165 行）在覆盖前检测变化、允许重发（两处对称改）：

```python
                if nalu_type == NALU_TYPE_SPS:
                    if config_sent and sps_data != nalu:
                        # 编码参数变化（卡死自愈/复位后重编）：重置标志以重发 config
                        config_sent = False
                    sps_data = nalu
```

```python
                elif nalu_type == NALU_TYPE_PPS:
                    if config_sent and pps_data != nalu:
                        config_sent = False
                    pps_data = nalu
```

（其余行保持原样；两分支尾部的 `if sps_data and pps_data and not config_sent: await _send_config(...)` 不动。）

- [ ] **Step 9: 补 video.py 的 Fake 与测试**

`tests/unit/test_video.py` 的 `FakeStreamService` 加：

```python
    def idle_reset_seconds(self) -> float:
        return 5.0
```

追加 WS 集成测试（沿用文件内 `video_client` fixture 与 `FakeEncoder` 推流方式）：喂入一次 SPS/PPS/帧后收到 config 消息且含 `"idle_reset_seconds": 5.0`；再次喂入**不同 SPS** + 帧，断言收到第二条 config。

- [ ] **Step 10: 全量后端门禁**

Run: `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80 && ruff check backend/app && mypy backend/app`
Expected: PASS / 0 / 0

- [ ] **Step 11: 提交**

```bash
git add backend/app/scrcpy/control_sender.py config/settings.py config/base.yaml \
  backend/app/infrastructure/stream/scrcpy.py backend/app/application/stream_service.py \
  backend/app/interfaces/ws/video.py tests/scrcpy/test_scrcpy_encoder.py \
  tests/unit/test_metrics_config.py tests/unit/test_video.py
git commit -m "$(cat <<'EOF'
feat: 后端空闲保活 RESET_VIDEO 与 config 透传（方案 19 实施项 1a）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 卡死探针 + StreamService 防风暴自愈（1b）

**Files:**
- Modify: `backend/app/infrastructure/stream/scrcpy.py`（异常类 + 探针分支 + re-raise）
- Modify: `backend/app/application/stream_service.py`（`_stall_restarts`、start_stream 重启分支）
- Test: `tests/scrcpy/test_scrcpy_encoder.py`、`tests/application/test_stream_adaptive.py`

**Interfaces:**
- Consumes: Task 1 的保活循环与 `reset_sent_at` 状态
- Produces: `EncoderStalledError`（定义于 `scrcpy.py`，供 StreamService import）；卡死重启时 `get_stream_epoch()` +1（WS 层既有逻辑自动重置 parser/config_sent）。

- [ ] **Step 1: 写失败测试（探针上抛）**

`tests/scrcpy/test_scrcpy_encoder.py` 追加（复用 Task 1 用例的同一 harness 装配，仅断言不同）：

```python
async def test_stall_probe_raises_after_reset(monkeypatch):
    """RESET_VIDEO 后仍无数据 ≥ idle_reset → 生成器抛 EncoderStalledError。"""
    import pytest, types
    from app.infrastructure.stream.scrcpy import EncoderStalledError
    fake = types.SimpleNamespace(stream=types.SimpleNamespace(
        idle_reset_seconds=0.2, raw_stream_fallback=False))
    monkeypatch.setattr("app.infrastructure.stream.scrcpy.settings", lambda: fake)
    conn = ConnectionHarness(...)   # 同 Task 1：1 帧后永久静默
    with pytest.raises(EncoderStalledError):
        await conn.consume_until_done(timeout=2.0)  # 迭代生成器直到异常/超时
```

断言目标：首帧后 0.2s（发 RESET）+0.2s（探针）量级内抛 `EncoderStalledError`，2s 超时兜底。harness 消费方法名按文件实际 API 适配，语义为「迭代到生成器结束」。

- [ ] **Step 2: 写失败测试（StreamService 自愈与防风暴）**

`tests/application/test_stream_adaptive.py` 追加（沿用 `FakeEncoder.created` 类计数与 `stream_settings` fixture 模式）：

```python
async def test_stall_recovery_and_storm_guard(stream_settings, monkeypatch):
    import asyncio, pytest
    from app.application.stream_service import StreamService
    from app.infrastructure.stream.scrcpy import EncoderStalledError

    async def fake_sleep(t): pass
    monkeypatch.setattr("app.application.stream_service.asyncio.sleep", fake_sleep)

    class StallOnly:
        created = 0
        def __init__(self):
            StallOnly.created += 1
            self.n = StallOnly.created
        async def start(self, device_id, opts):
            yield b"f"
            raise EncoderStalledError("stalled")
        async def stop(self): pass

    StallOnly.created = 0
    svc = StreamService(encoder_factory=StallOnly)
    gen = svc.start_stream("dev")
    frames = []
    with pytest.raises(EncoderStalledError):
        async for f in gen:
            frames.append(f)
    # 首次 + 3 次防风暴窗口内重启 = 4 台编码器、4 个首帧，随后异常传播
    assert frames == [b"f"] * 4
    assert StallOnly.created == 4
```

- [ ] **Step 3: 运行确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/scrcpy/test_scrcpy_encoder.py tests/application/test_stream_adaptive.py -q`
Expected: FAIL（`EncoderStalledError` 未定义 / 无重启行为）

- [ ] **Step 4: 实现探针（scrcpy.py）**

`scrcpy.py` 模块级（logger 定义之后）加：

```python
class EncoderStalledError(RuntimeError):
    """编码器卡死：发出 RESET_VIDEO 探针后仍超过 idle_reset 秒无数据
    （E009/E021 同族「活着不产帧」故障，方案 19 实施项 1b）。"""
```

Task 1 保活分支中，在 `if reset_sent_at is None:` 块之后（同一 `except asyncio.TimeoutError` 内）补探针：

```python
                    elif now - reset_sent_at >= idle_reset:
                        raise EncoderStalledError(
                            f"no data {now - reset_sent_at:.1f}s after RESET_VIDEO")
```

外层异常处理（原 453-454 行）之前插入 re-raise 分支（探针异常不能被 `stream_error` 吞掉）：

```python
        except EncoderStalledError as e:
            logger.error("encoder_stalled", device=device_id, error=str(e))
            raise
        except Exception as e:
            logger.error("stream_error", device=device_id, error=str(e))
```

- [ ] **Step 5: 实现 StreamService 自愈**

`stream_service.py` import 区改为：

```python
from collections import deque
from app.infrastructure.stream.scrcpy import EncoderStalledError, ScrcpyEncoder
```

`__init__` 加：

```python
        # 卡死自愈（方案 19 实施项 1b）：device → 最近卡死重启时刻（60s 滑窗防风暴）
        self._stall_restarts: dict[str, deque[float]] = {}
```

`start_stream` 内层 132-175 行改为：

```python
                restart_bitrate = None
                stalled = False
                stall_exc: EncoderStalledError | None = None
                frame_task = None
                event_task = None
                try:
                    agen = encoder.start(device_id, opts)
                    while self.active_streams.get(device_id):
                        if self._pending_bitrate.get(device_id) is None:
                            restart_event.clear()
                        frame_task = asyncio.ensure_future(agen.__anext__())
                        event_task = asyncio.ensure_future(restart_event.wait())
                        done, _ = await asyncio.wait(
                            {frame_task, event_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if frame_task in done:
                            try:
                                frame = frame_task.result()
                            except StopAsyncIteration:
                                break
                            yield frame
                        else:
                            pend = self._pending_bitrate.pop(device_id, None)
                            if pend is None or pend == current_bps:
                                continue
                            restart_bitrate = pend
                            break
                except EncoderStalledError as e:
                    stalled = True
                    stall_exc = e
                    logger.warning("encoder_stalled_detected",
                                   device=device_id, error=str(e))
                finally:
                    for t in (frame_task, event_task):
                        if t is not None and not t.done():
                            t.cancel()
                    await encoder.stop()
                    self.encoders.pop(device_id, None)
                if not stalled:
                    if restart_bitrate is None:
                        break
                    current_bps = restart_bitrate
                    self._epoch[device_id] = self._epoch.get(device_id, 0) + 1
                    logger.info(
                        "adaptive_bitrate_restarting_encoder",
                        device=device_id,
                        bit_rate=current_bps,
                        epoch=self._epoch[device_id],
                    )
                    continue
                now = time.monotonic()
                hist = self._stall_restarts.setdefault(device_id, deque())
                while hist and now - hist[0] > 60.0:
                    hist.popleft()
                if len(hist) >= 3:
                    assert stall_exc is not None
                    raise stall_exc
                if hist:
                    await asyncio.sleep(1.0 * len(hist))
                hist.append(now)
                self._epoch[device_id] = self._epoch.get(device_id, 0) + 1
                logger.info("stall_restart_encoder",
                            device=device_id, epoch=self._epoch[device_id],
                            recent_stalls=len(hist))
```

外层 `finally`（176-182 行）加清理：

```python
            self._stall_restarts.pop(device_id, None)
```

- [ ] **Step 6: 门禁**

Run: `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80 && ruff check backend/app && mypy backend/app`
Expected: 全绿。注意既有断流/停止类测试不得回归（`raise stall_exc` 只在探针触发；`stop_stream`、`StopAsyncIteration` 路径不变）。

- [ ] **Step 7: 提交**

```bash
git add backend/app/infrastructure/stream/scrcpy.py backend/app/application/stream_service.py \
  tests/scrcpy/test_scrcpy_encoder.py tests/application/test_stream_adaptive.py
git commit -m "$(cat <<'EOF'
feat: 编码器卡死探针与防风暴自愈重启（方案 19 实施项 1b）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 前端回退阈值与保活间隔联动（2a，含 2b 裁定落地）

**Files:**
- Modify: `frontend/src/services/videoFallback.ts`
- Modify: `frontend/src/services/h264VideoStream.ts`（config 解析 + getter）
- Modify: `frontend/src/components/stream/VideoPlayer.vue`（`checkH264Fallback` 传参）
- Test: `frontend/src/services/videoFallback.test.ts`（无则新建，风格沿用 `h264VideoStream.test.ts`）

**Interfaces:**
- Consumes: Task 1 后端 config 消息新字段 `idle_reset_seconds`
- Produces: `computeFallbackThresholds(keepaliveMs): FallbackThresholds`；`evaluateH264Fallback(input, thresholds?)`；`H264VideoStream.keepaliveIntervalMs: number`——Task 5/验收依赖。

- [ ] **Step 1: 写失败测试**

`frontend/src/services/videoFallback.test.ts`：

```ts
import { describe, expect, it } from 'vitest'
import {
  FALLBACK_THRESHOLDS,
  computeFallbackThresholds,
  evaluateH264Fallback,
} from './videoFallback'

describe('computeFallbackThresholds', () => {
  it('keepalive=0 时返回默认阈值', () => {
    expect(computeFallbackThresholds(0)).toEqual({
      STALL_MS: 3000, NO_STREAM_MS: 8000, HARD_TIMEOUT_MS: 15000,
    })
  })
  it('keepalive=5000 → 10000/12000/17000', () => {
    expect(computeFallbackThresholds(5000)).toEqual({
      STALL_MS: 10000, NO_STREAM_MS: 12000, HARD_TIMEOUT_MS: 17000,
    })
  })
  it('小 keepalive 不低于默认下限', () => {
    expect(computeFallbackThresholds(1000)).toEqual(FALLBACK_THRESHOLDS)
  })
})

describe('evaluateH264Fallback 自定义阈值', () => {
  it('静止设备在抬升的 stall 阈值下不误判', () => {
    const base = {
      state: 'streaming' as const, frameCount: 10, lastFrameTime: 0,
      startedAt: 0, now: 5000,
    }
    expect(evaluateH264Fallback({ ...base, lastFrameTime: 1000 }).fallback).toBe(true)
    const t = computeFallbackThresholds(5000)
    expect(evaluateH264Fallback({ ...base, lastFrameTime: 1000 }, t).fallback).toBe(false)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run src/services/videoFallback.test.ts`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 实现 videoFallback.ts**

`FALLBACK_THRESHOLDS` 定义后加：

```ts
export interface FallbackThresholds {
  STALL_MS: number
  NO_STREAM_MS: number
  HARD_TIMEOUT_MS: number
}

/**
 * 与后端空闲保活联动的回退阈值（方案 19 实施项 2a）。
 * keepaliveMs 为后端 RESET_VIDEO 保活间隔（config.idle_reset_seconds×1000）；
 * 静止设备在保活下仍有周期性 IDR，STALL 取 2× 间隔，再叠加
 * 2s/5s 余量保证 NO_STREAM < HARD 单调。keepaliveMs=0（保活关闭）回默认。
 */
export function computeFallbackThresholds(keepaliveMs: number): FallbackThresholds {
  const STALL_MS = Math.max(3000, keepaliveMs * 2)
  const NO_STREAM_MS = Math.max(8000, STALL_MS + 2000)
  const HARD_TIMEOUT_MS = Math.max(15000, NO_STREAM_MS + 5000)
  if (keepaliveMs <= 0) {
    return { STALL_MS: 3000, NO_STREAM_MS: 8000, HARD_TIMEOUT_MS: 15000 }
  }
  return { STALL_MS, NO_STREAM_MS, HARD_TIMEOUT_MS }
}
```

`evaluateH264Fallback` 签名与三处常量引用改为：

```ts
export function evaluateH264Fallback(
  input: FallbackInput,
  thresholds: FallbackThresholds = FALLBACK_THRESHOLDS,
): FallbackResult {
```

体内 `FALLBACK_THRESHOLDS.STALL_MS → thresholds.STALL_MS`、`HARD_TIMEOUT_MS → thresholds.HARD_TIMEOUT_MS`、`NO_STREAM_MS → thresholds.NO_STREAM_MS`。

- [ ] **Step 4: 实现 h264VideoStream.ts 解析与 getter**

类字段区加 `private _keepaliveMs = 0`。config 分支（解析 `msg.type === 'config'` 处）加：

```ts
      this._keepaliveMs = Number(msg.idle_reset_seconds ?? 0) * 1000
```

（suspend 期间的 config 缓存路径同样生效即可。）public getter 区加：

```ts
  /** 后端 RESET_VIDEO 保活间隔（ms），0 表示未知/关闭（供回退阈值联动） */
  get keepaliveIntervalMs(): number {
    return this._keepaliveMs
  }
```

- [ ] **Step 5: 实现 VideoPlayer.vue 传参**

`checkH264Fallback()`（201-222 行）中调用改为：

```ts
    const result = evaluateH264Fallback(
      {
        state: h264State, frameCount, lastFrameTime,
        startedAt: h264StartedAt, now: Date.now(),
      },
      computeFallbackThresholds(h264Stream.keepaliveIntervalMs),
    )
```

（实参名以文件现状为准；import 补 `computeFallbackThresholds`。）

- [ ] **Step 6: 前端门禁**

Run: `cd frontend && npx vitest run && npm run lint && npm run build`
Expected: 全绿（`evaluateH264Fallback` 旧调用零参仍走默认阈值，既有测试不回归）。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/services/videoFallback.ts frontend/src/services/videoFallback.test.ts \
  frontend/src/services/h264VideoStream.ts frontend/src/components/stream/VideoPlayer.vue
git commit -m "$(cat <<'EOF'
feat: 前端回退阈值与后端保活间隔联动（方案 19 实施项 2a）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: resume 请求关键帧 request_keyframe（实施项 3）

**Files:**
- Modify: `backend/app/infrastructure/stream/scrcpy.py`（`request_keyframe`）
- Modify: `backend/app/application/stream_service.py`（防抖 1s）
- Modify: `backend/app/interfaces/ws/video.py`（`_handle_input` 分支）
- Modify: `frontend/src/services/h264VideoStream.ts`（`resume()` 发送）
- Test: `tests/application/test_stream_adaptive.py`（或新文件 `tests/application/test_stream_keyframe.py`）、`tests/unit/test_video.py`、`frontend/src/services/h264VideoStream.test.ts`

**Interfaces:**
- Consumes: Task 1 `ControlSender.reset_video()`
- Produces: `ScrcpyEncoder.request_keyframe() -> None`；`StreamService.request_keyframe(device_id, now=None) -> bool`；WS 入站消息 `{ "op": "request_keyframe" }`。

- [ ] **Step 1: 写失败测试（后端服务层）**

`tests/application/test_stream_keyframe.py`（新建，风格沿用 `test_stream_adaptive.py`）：

```python
async def test_request_keyframe_debounced_1s():
    import pytest
    from app.application.stream_service import StreamService

    class KFEncoder:
        def __init__(self): self.calls = 0
        async def request_keyframe(self): self.calls += 1
        async def stop(self): pass

    svc = StreamService()
    enc = KFEncoder()
    svc.encoders["dev"] = enc
    assert await svc.request_keyframe("dev", now=100.0) is True
    assert await svc.request_keyframe("dev", now=100.5) is False   # 防抖窗口内
    assert await svc.request_keyframe("dev", now=101.1) is True
    assert enc.calls == 2
    assert await svc.request_keyframe("missing", now=200.0) is False


async def test_encoder_request_keyframe_noop_without_control():
    from app.infrastructure.stream.scrcpy import ScrcpyEncoder
    enc = ScrcpyEncoder()
    await enc.request_keyframe()  # _control_sender 为 None → no-op 不抛
```

- [ ] **Step 2: 写失败测试（WS 分发 + 前端）**

`tests/unit/test_video.py`：`FakeStreamService` 加 `async def request_keyframe(self, device_id, now=None): self.keyframe_requests.append(device_id); return True`（`__init__` 里初始化 list）。追加用例：`video_client` 发送 `{"op": "request_keyframe"}` → 断言 `fake.keyframe_requests == [device_id]`。

`frontend/src/services/h264VideoStream.test.ts` 追加（沿用 FakeWebSocket.instances 模式）：实例进入 streaming → `suspend()` → `resume()` → 断言 `FakeWebSocket.instances.at(-1).sent` 含 `{"op":"request_keyframe"}`，且早于任何解码器重建。

- [ ] **Step 3: 运行确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/application/test_stream_keyframe.py tests/unit/test_video.py -q` 与 `cd frontend && npx vitest run src/services/h264VideoStream.test.ts`
Expected: FAIL

- [ ] **Step 4: 实现后端**

`scrcpy.py` 控制输入方法区（`send_input` 之后）加：

```python
    async def request_keyframe(self) -> None:
        """立即催出新 IDR（前端回切场景，方案 19 实施项 3）；无控制通道时 no-op。"""
        if self._control_sender is not None:
            await self._control_sender.reset_video()
```

`stream_service.py` `__init__` 加 `self._last_keyframe_at: dict[str, float] = {}`；方法区加：

```python
    async def request_keyframe(self, device_id: str, now: float | None = None) -> bool:
        """
        客户端请求关键帧（resume 回切），经 RESET_VIDEO 实现。

        最小间隔 1s 防抖（方案 19 实施项 3 冷却约定）；返回是否实际发送。
        """
        encoder = self.encoders.get(device_id)
        if encoder is None:
            return False
        if now is None:
            now = time.monotonic()
        last = self._last_keyframe_at.get(device_id)
        if last is not None and now - last < 1.0:
            return False
        self._last_keyframe_at[device_id] = now
        await encoder.request_keyframe()
        return True
```

外层 `finally` 加 `self._last_keyframe_at.pop(device_id, None)`。

`video.py` `_handle_input` 中 `if data.get("op") == "stats":` 块之前加：

```python
    if data.get("op") == "request_keyframe":
        await stream_service.request_keyframe(device_id)
        return
```

- [ ] **Step 5: 实现前端**

`h264VideoStream.ts` `resume()`（249 行）中 `this._suspended = false` 之后加：

```ts
    // 回切即新接收端加入：请求服务端立即产出 IDR，消灭 configuring 长等待
    this.ws.send({ op: 'request_keyframe' })
```

（每次 resume 天然只发一次——`resume()` 有 `_suspended` 前置守卫。）

- [ ] **Step 6: 全量门禁（前后端）**

Run: 后端三连 + `cd frontend && npx vitest run && npm run lint && npm run build`
Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add backend/app/infrastructure/stream/scrcpy.py backend/app/application/stream_service.py \
  backend/app/interfaces/ws/video.py frontend/src/services/h264VideoStream.ts \
  tests/application/test_stream_keyframe.py tests/unit/test_video.py \
  frontend/src/services/h264VideoStream.test.ts
git commit -m "$(cat <<'EOF'
feat: resume 回切请求关键帧 request_keyframe（方案 19 实施项 3）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 状态提示治理（实施项 4）

**Files:**
- Modify: `frontend/src/components/stream/VideoPlayer.vue`（模板 49 行覆盖层、`startScreenshotMode` 300 行、`stateLabel`）
- Test: `frontend/src/components/stream/VideoPlayer.test.ts`

**Interfaces:**
- Consumes: 既有 `state` ref 与截图模式切换函数
- Produces: 新状态值 `'screenshot'`；`hasStreamedOnce: Ref<boolean>`——Task 7 重连提示可复用。

- [ ] **Step 1: 写失败测试**

`VideoPlayer.test.ts` 追加（沿用 vi.hoisted 类 mock 风格）：

```ts
it('回退截图模式不闪现「正在连接」', async () => {
  // 驱动组件进入 streaming 后触发 checkH264Fallback → startScreenshotMode
  // 断言 document 不含「正在连接」，且覆盖层不显示
})
it('已出过画面后回切 configuring 显示「正在恢复画面…」', async () => {
  // streaming → resume 回 configuring，断言文案为「正在恢复画面…」而非「正在连接...」
})
```

两用例的 mock 装配与状态推进照抄该文件现有「回退截图」用例（`h264Stream` 与 `videoStream` 两个 hoisted mock），仅断言文本不同。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run src/components/stream/VideoPlayer.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

`VideoPlayer.vue`：

1. script 区加 `const hasStreamedOnce = ref(false)`；在 state 变为 `'streaming'` 的唯一路径（`attachH264Handlers` 的 onStateChange 或 `watch(state, ...)`）中：

```ts
  if (s === 'streaming') hasStreamedOnce.value = true
```

2. `startScreenshotMode()`（300 行）删除 `state.value = 'idle'`，改为：

```ts
  state.value = 'screenshot'
```

（`state` 的联合类型同步补 `'screenshot'`；`videoStream.start()` 内部会同步置 streaming 回调，不影响此处覆盖层判断——覆盖层条件不含 `'screenshot'`。）

3. 模板 49 行覆盖层改为区分文案：

```html
      <div v-if="state === 'idle' || state === 'configuring'" class="stream-overlay">
        <span v-if="!hasStreamedOnce">正在连接...</span>
        <span v-else class="recovering-tip">正在恢复画面…</span>
      </div>
```

（沿用原覆盖层实际 class 名；`recovering-tip` 加角标样式：绝对定位右上角、半透明小字，保留下层冻结帧可见。）

4. `stateLabel` 映射补 `screenshot: '截图模式'`（若存在该映射；按文件现状对齐）。

- [ ] **Step 4: 前端门禁**

Run: `cd frontend && npx vitest run && npm run lint && npm run build`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/stream/VideoPlayer.vue \
  frontend/src/components/stream/VideoPlayer.test.ts
git commit -m "$(cat <<'EOF'
feat: 「正在连接」仅首次连接显示，恢复期改为角标提示（方案 19 实施项 4）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 后端 stream_ended 通知 + 前端消费（实施项 5 之流结束）

**Files:**
- Modify: `backend/app/interfaces/ws/video.py`（async-for 正常结束处）
- Modify: `frontend/src/services/h264VideoStream.ts`（`handleJsonMessage` 新分支）
- Test: `tests/unit/test_video.py`、`frontend/src/services/h264VideoStream.test.ts`

**Interfaces:**
- Consumes: 既有 error → 回退链
- Produces: 出站消息 `{ "type": "stream_ended" }`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_video.py`：FakeEncoder 的 `start()` 产完帧后正常 return（非挂起）。`video_client` 连接消费帧后，断言收到的 JSON 序列末尾含 `{"type": "stream_ended"}`。

`h264VideoStream.test.ts`：向 FakeWebSocket 注入文本消息 `{"type":"stream_ended"}` → 断言 `state === 'error'`（走既有 setError）。

- [ ] **Step 2: 运行确认失败**

Run: 上述两测试文件对应命令
Expected: FAIL

- [ ] **Step 3: 实现**

`video.py` 200-202 行 `logger.info("video_stream_ended", ...)` 之后加：

```python
        try:
            await websocket.send_json({"type": "stream_ended"})
        except Exception:
            pass
```

`h264VideoStream.ts` JSON 消息处理链（`restarting` 分支旁）加：

```ts
      } else if (msg.type === 'stream_ended') {
        // 后端编码器/流已结束：立即报错走回退链，不再等 watchdog（方案 19 实施项 5）
        this.setError('视频流已结束')
```

- [ ] **Step 4: 全量门禁 + 提交**

Run: 后端三连 + 前端三连，Expected 全绿。

```bash
git add backend/app/interfaces/ws/video.py tests/unit/test_video.py \
  frontend/src/services/h264VideoStream.ts frontend/src/services/h264VideoStream.test.ts
git commit -m "$(cat <<'EOF'
feat: 流结束通知 stream_ended，前端立即走回退链（方案 19 实施项 5）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: WebSocket 指数退避自动重连（实施项 5 之连接层）

**Files:**
- Modify: `frontend/src/services/websocket.ts`
- Test: `frontend/src/services/websocket.test.ts`

**Interfaces:**
- Consumes: 既有 `connect/send/close` API（对外签名不变）
- Produces: `setReconnectHandler(handler: () => void)`；重连成功后自动重跑 `connect()` 并回调 handler（新连接服务端会重发 config，h264 解码链路凭既有 config 分支自愈，无需额外接线）。

- [ ] **Step 1: 写失败测试**

`websocket.test.ts` 追加（沿用 FakeWebSocket.instances + `vi.useFakeTimers()`）：

```ts
it('非手动关闭后按 1s/2s/4s 指数退避重连，30s 封顶', () => {
  vi.useFakeTimers()
  const svc = new WebSocketService('ws://x')
  svc.connect()
  const first = FakeWebSocket.instances[0]
  first.simulateClose()
  expect(FakeWebSocket.instances.length).toBe(1)   // 立即不重连
  vi.advanceTimersByTime(1000)
  expect(FakeWebSocket.instances.length).toBe(2)   // 第 1 次 1s 后
  FakeWebSocket.instances[1].simulateClose()
  vi.advanceTimersByTime(2000)
  expect(FakeWebSocket.instances.length).toBe(3)   // 第 2 次 2s 后
  vi.advanceTimersByTime(30000)
  // （再断连一次并快进到 4000ms 验证 2^2=4s；封顶用例单独测 attempts=5 → 30000）
  vi.useRealTimers()
})

it('onopen 重置退避计数；手动 close() 不再重连；重连后触发 reconnect handler', () => {
  // instances[n].simulateOpen() 后再 close → 下一次延迟回到 1000ms
  // svc.close() 后 advanceTimersByTime(60000) → instances 数量不变
  // svc.setReconnectHandler(spy) → 重连定时器触发后 spy 被调用
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run src/services/websocket.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现 websocket.ts**

类字段区加：

```ts
  private reconnectAttempts = 0
  private manualClose = false
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private onReconnect: (() => void) | null = null
```

`connect()` 中：`onopen` 回调体首行加 `this.reconnectAttempts = 0`；`onclose` 回调改为：

```ts
    this.ws.onclose = () => {
      console.log('[WS] Closed:', this.url)
      if (this.onClose) {
        this.onClose()
      }
      if (!this.manualClose) {
        this.scheduleReconnect()
      }
    }
```

类内加：

```ts
  /** 注册重连成功回调（重连走同一 connect 流程后触发） */
  setReconnectHandler(handler: () => void) {
    this.onReconnect = handler
  }

  private scheduleReconnect() {
    const delay = Math.min(30000, 1000 * 2 ** this.reconnectAttempts)
    this.reconnectAttempts += 1
    console.log(`[WS] Reconnect scheduled in ${delay}ms (attempt ${this.reconnectAttempts})`)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
      this.onReconnect?.()
    }, delay)
  }
```

`close()` 改为：

```ts
  close() {
    this.manualClose = true
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }
```

注意：重连 `connect()` 会新建原生 WebSocket 并在构造流程内重设 `binaryType='arraybuffer'`（connect 已有）；h264VideoStream 若在 open 后另有 `setBinaryType` 调用路径，重连后由 `onReconnect` 无额外动作覆盖——新 config 消息驱动解码器重建（见 4.2 裁定：1Hz keepalive 心跳**不做**）。

- [ ] **Step 4: 前端门禁 + 提交**

Run: `cd frontend && npx vitest run && npm run lint && npm run build`
Expected: 全绿

```bash
git add frontend/src/services/websocket.ts frontend/src/services/websocket.test.ts
git commit -m "$(cat <<'EOF'
feat: WebSocket 指数退避自动重连（方案 19 实施项 5）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 真机验证与文档回写

**Files:**
- Modify: `方案/19-推流连接稳定性优化.md`（状态行）
- Modify: `方案/README.md`（索引与更新记录）
- Modify: `方案/进度追踪.md`（第二十七次更新）
- Modify: `docs/API.md` §3.1（WS 消息表）
- Create: `tests/manual/verify_stream_stability_19.md`（真机验证清单，可选脚本注释）

**Interfaces:**
- Consumes: Task 1-7 全部产出
- Produces: 验收结论回写；无代码接口。

- [ ] **Step 1: 全量门禁复核**

Run: 后端三连 + 前端三连
Expected: 全绿（这是实施项 5 验收标准第 5 条）

- [ ] **Step 2: .25 真机验证（浏览器实测，用户在场时进行）**

前置：后端 `python run_server.py`，Chrome 打开并连接 `192.168.8.25:5555`。按验收标准逐项：

1. **静止 10 分钟**：画面保持最后帧冻结、不进截图模式、无「正在连接」；后端日志每 ~5s 一条 `idle_reset_video_sent`；浏览器 console 观察 fallback 日志无触发。
2. **回切**：制造画面活动 → 确认恢复回切在 request_keyframe 后 ≤2s 出图（对照「正在恢复画面…」角标出现与消失时间）。
3. **卡死注入**：`adb -s 192.168.8.25:5555 shell` 找 scrcpy-server PID（读 `/proc/*/cmdline` 匹配 scid），`kill -STOP` 其编码线程（`/proc/PID/task` 逐个 STOP）；≤2×idle_reset 秒内观察 `encoder_stalled` → `stall_restart_encoder` 日志与新 IDR 恢复；60s 内 4 连停触发 error 回退链（防风暴生效）。恢复用 `kill -CONT`。
4. **带宽**：静止期设备侧 tx_bytes 差分估算 ≤100kbps（RESET IDR ≈23KB@5s ≈37kbps）。
5. **.18 回归**：连接 192.168.8.18:5555，确认 0.4fps 故障型设备在探针下走「卡死→重启→error→截图回退」链且无死循环。

任何一项不达标 → 回到对应 Task 修复（阈值参数可在不违反裁定 2 公式前提下微调系数并回写方案文档）。

- [ ] **Step 3: 文档回写**

1. `方案/19-推流连接稳定性优化.md` 状态行改「已实施（YYYY-MM-DD 真机验证通过）」，第五节风险项标注验证结论。
2. `docs/API.md` §3.1 追加三行：config 字段 `idle_reset_seconds`；入站 `{"op":"request_keyframe"}`；出站 `{"type":"stream_ended"}`。
3. `方案/README.md`：19 条目状态更新，更新记录表补一行。
4. `方案/进度追踪.md`：第二十七次更新——实施计划执行记录（任务清单、门禁、真机结论）。
5. `tests/manual/verify_stream_stability_19.md`：落盘 Step 2 清单与实测输出。

- [ ] **Step 4: 提交**

```bash
git add 方案/ docs/API.md tests/manual/verify_stream_stability_19.md
git commit -m "$(cat <<'EOF'
docs: 方案 19 真机验证结论回写与 API 文档更新

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## 验收标准对照（方案 19 第四节）

| # | 标准 | 覆盖任务 |
|---|------|----------|
| 1 | 静止 10 分钟无回退、无「正在连接」、帧冻结 | T1 + T3 + T5，T8 实测 |
| 2 | 回切 request_keyframe → 出图 ≤2s | T4，T8 实测 |
| 3 | 卡死注入 ≤2×idle_reset 自动重启恢复 | T2，T8 实测 |
| 4 | 保活带宽 ≤100kbps | T1（5s 间隔实测 37kbps），T8 实测 |
| 5 | 既有门禁全绿 | 各任务 Step 门禁 + T8 Step 1 |

## 风险执行提示

- **RESET 与真实帧竞态**：T1 实现中任何数据到达即复位 `reset_sent_at`/`last_data_at`，竞态窗口 = 单次 tick（≤2.5s），满足探针语义；不得改成按「帧类型」过滤。
- **StopAsyncIteration 语义不变**：T2 的 stalled 分支只捕获 `EncoderStalledError`，编码器进程退出（sentinel/EOF）仍走原 break 路径，勿混用。
- **定制 jar 锁 4.1**：若未来升级 scrcpy 版本，RESET_VIDEO type 值需复核（方案第五节）。
- **Task 3 阈值联动依赖 Task 1 的 config 字段**：跨层契约仅 `idle_reset_seconds` 一个数字，前端对缺字段（旧后端）以 0 处理回默认阈值——天然向后兼容。

