# 方案 31：前端丢帧策略与 warn 风暴治理（P2-3）

- 日期：2026-09-30
- 状态：**✅ 已实施收口（2026-09-30）**。TDD 实施（commit 82fb4a2，
  单测 +10：追赶状态机 7 / warn 限速 3）；浏览器侧 e2e 验收多轮通过
  （.18 CPU 6× throttle：进追赶→IDR 恢复 **0.91–1.11s**、催帧恰一次/
  追赶周期、warn 0 条）+ .25 静止回归 + 方案 19 回切链回归——见 §四
- 关联：[17-推流流畅度优化](./17-推流流畅度优化.md)（实施项 2 丢帧初版）、
  [docs/性能对标与优化清单.md](../docs/性能对标与优化清单.md)（P2 第 3 项）、
  `frontend/src/services/h264VideoStream.ts`、`frontend/src/services/h264NalUtils.ts`

## 一、问题

### 1.1 丢帧策略：水位驱动破坏参考链

现状（h264VideoStream.ts:431-434）：`shouldDropFrame` 按解码队列水位
逐帧判定——delta 帧且 `decodeQueueSize ≥ 2` 时丢弃，队列回落后立即
恢复提交。缺陷：

- **丢一帧即断链**：H.264 P 帧引用前一参考帧。丢 F3（未提交）后，
  F4、F5 的参考缺失；一旦队列回落恢复提交 F4，解码器以错误参考
  解码 → 花屏/错误隐藏，**且后续 delta 沿错链继续**，直到 IDR 才
  干净（设备 IDR 间隔活动态 0.5-10s、静止态 42-57s）。
- **水位抖动的反复起停**：队列 2→1→2 时丢/解交替，链断与错解反复，
  画面表现为「花屏闪动」而非稳定降级。
- 正确策略：**一旦进入丢帧，锁定丢弃所有 delta 直到下一个 IDR**
  （参考链的唯一干净恢复点），即 P2 清单所指「丢到下一 IDR」跳帧策略。

### 1.2 warn 控制台风暴

- `:412`「Frame dropped: decoder not ready」解码器未配置期间每帧 warn；
- `:491`「Frame size mismatch with canvas」尺寸不匹配期间每帧 warn；
- 两处均无去重/限速，长窗口场景（重连宽限、异常尺寸）可形成每秒
  数十条 warn 风暴，淹没有效日志、拖慢控制台。

## 二、设计

### 2.1 追赶状态机（丢到下一 IDR）

h264VideoStream 新增 `_awaitingIdr: boolean`：

| 状态 | 事件 | 动作 |
|---|---|---|
| 正常 | delta 且 `queue ≥ MAX_DECODE_QUEUE`(=2) | 丢帧 + 进入追赶 + 发 `{op:'request_keyframe'}` 催 IDR |
| 追赶中 | delta（任意水位） | 一律丢弃（不判水位） |
| 追赶中 | IDR 到达 | 退出追赶；`queue ≥ MAX` 走既有重建保护，否则直接提交 IDR（自包含，干净恢复） |

- **催帧依据**：后端 `request_keyframe` 走 RESET_VIDEO，实测 0.1-0.4s
  出新 IDR（E024）；前端 resume 路径已有同款用法（h264VideoStream.ts:267），
  后端 1s 防抖防风暴（方案 19 实施项 3）。追赶窗口由「设备 IDR 间隔
  （10s/57s）」压缩到「催帧往返（≤ ~1s）」，恢复时间有界。
- **体验权衡**：追赶期间画面冻结在最后解码帧（不花屏、不旧链错解）；
  积压本身已意味着延迟累积，冻结至干净恢复点优于花屏闪动。
- **状态清理**：`initDecoder()`（config 重发 / resume 重建 / IDR 重建）
  中重置 `_awaitingIdr`——每条重建路径都回到干净初始态。
- **阈值不变**：`MAX_DECODE_QUEUE = 2` 保持（方案 17 实施项 2 既定值）。

### 2.2 warn 限速

- `decoder not ready`：进入未就绪态后首次 warn（附「后续丢弃将静默
  计数」），恢复 configured 后重置标志；
- `frame size mismatch`：首次 warn + 累计计数，尺寸恢复匹配后重置；
- 判据按「状态转换」而非计时——状态性问题（未就绪/尺寸错配）的
  warn 语义就是「进入了异常态」，一次足够。

## 三、测试方向（TDD）

`frontend/src/services/h264VideoStream.test.ts`（FakeVideoDecoder /
FakeWs 基建已有）：

1. **追赶锁定**：queue=2 收 delta → 丢 + 进入追赶；随后 queue=0 收
   delta → **仍丢**（与现状水位行为的关键差异）；
2. **IDR 干净恢复**：追赶中收到 IDR → 提交（queue<2 直接提交；
   queue≥2 走重建保护）+ 退出追赶；
3. **催帧**：进入追赶时 ws 恰发一次 `{op:'request_keyframe'}`；
   追赶中后续丢弃不再发；
4. **状态清理**：initDecoder（config 重发 / resume）后追赶状态复位；
5. **warn 限速**：未就绪连续 N 帧 → warn 仅 1 次；恢复后再未就绪 →
   再 warn 1 次；尺寸错配同理；
6. **不回归**：正常路径（queue<2 delta 提交、关键帧、suspend 探针、
   restarting 宽限）既有用例全绿；vitest + vue-tsc + eslint 门禁。

## 四、真机/浏览器验收（✅ 2026-09-30 完成）

真机上难以构造可控积压（需要 CPU 争抢/多流同屏），验收以浏览器侧
制造（e2e spec：`tests/e2e/specs/frame-drop-31.spec.ts`）：

| 步骤 | 操作 | 期望 |
|---|---|---|
| 1 | Chrome DevTools CPU 6× throttle + .18 时钟页活动流（30fps） | 队列积压出现：控制台 warn 不再每帧风暴；画面冻结至催帧 IDR 后 ~1s 内恢复，无花屏闪动 |
| 2 | 恢复 CPU 正常 | 流恢复 30fps，droppedFrames 计数停止增长 |
| 3 | .25 静止流回归 | 无积压场景行为不变（无追赶误触发、无多余催帧） |
| 4 | 回退截图/回切链回归 | suspend/resume 路径不受影响（探针、request_keyframe 语义与方案 19 一致） |

### 4.2 实测结论

**场景 1+2（.18 时钟页 + CDP `Emulation.setCPUThrottlingRate 6`，
headless Chromium 软解）**——多轮验收（恢复延迟记录 0.91 / 1.04 /
1.11s），收口轮完整数据：

- 基线窗口（注入前 10s）：30.0fps、丢帧 0→0
- 注入后 t+8.54s 首次积压丢帧；**催帧 request_keyframe 注入后恰 1 次
  （t+8.24s），基线期 0 次**；**进追赶→IDR 恢复 1.11s**（催帧→出帧
  1.40s）——「冻结至催帧 IDR 后 ~1s 内恢复」达成（方案预期 ≤ ~1s 量级）
- 恢复窗口（10s）：帧 804→1104 = **30.0fps 满速回升**、丢帧 5→5
  停止增长；100ms 采样时间线 40 条：drops 0→5（入追赶锁定）后恒 5，
  frames 线性增长，throttle 全程 state=直播中（无截图回退/无「正在连接」）
- **warn 限速：not-ready 0 条 / size-mismatch 0 条**（多轮一致，风暴消除）；
  全程零 VideoDecoder error
- 多追赶周期行为（历史轮）：每周期恰催帧 1 次，后端 1s 防抖兜底；
  headless 软解轻度过载的恒态零星丢帧（历史轮 0.7-1.3/s）经「追赶 →
  催帧 → IDR」干净恢复——旧代码在该场景为水位抖动反复起停（花屏闪动）

**场景 3（.25 静止流 90s）**：帧 0→150（静止保活节奏）、丢帧 0、
催帧 0 次、warn 0 条、无解码错误——无追赶误触发、无多余催帧，
1 passed（1.7m）

**场景 4（回退/回切链回归）**：方案 19 e2e 卡死注入用例（.18）
1 passed——suspend/resume 重建路径经 `initDecoder()` 复位追赶状态、
request_keyframe 语义与方案 19 一致

## 五、实施范围

- **涉及文件**：`frontend/src/services/h264VideoStream.ts`（追赶状态机 +
  warn 限速）、`h264NalUtils.ts`（`shouldDropFrame` 按新语义调整或并入
  状态机，测试同步）、`h264VideoStream.test.ts`；
- **后端零改动**（request_keyframe 已具备）；协议零改动；
- **不做**：改 MAX_DECODE_QUEUE 阈值、丢帧上报后端（P2-1 已证伪信号）、
  解码器输出端丢帧（WebCodecs 无廉价丢输出通道，省不了解码 CPU）。

## 六、风险与边界

| 风险 | 缓解 |
|---|---|
| 追赶期间画面冻结用户感知为「卡」 | 冻结为限至催帧 IDR（≤~1s）；花屏闪动体验更差；单测 + DevTools 验证恢复时间 |
| 催帧风暴（持续积压反复进入追赶） | 后端 1s 防抖（方案 19）；追赶中不重复催 |
| 设备不支持 RESET_VIDEO 或催帧失败 | 追赶退化等待设备自然 IDR（现状行为），不引入新故障路径 |
| 误触发（瞬时 queue=2 但马上恢复） | 触发即为真实积压（queue=2 意味着 2 帧未解完）；催帧一次成本低，按 IDR 恢复保守 |