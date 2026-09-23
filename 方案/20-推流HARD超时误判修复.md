# 方案 20：推流 HARD 超时误判修复（呼吸循环根因治理）

- 日期：2026-09-23
- 状态：设计待审
- 关联：[19-推流连接稳定性优化](./19-推流连接稳定性优化.md)、
  `tests/manual/verify_stream_stability_19.md`（真机验收记录）、
  [17-推流流畅度优化](./17-推流流畅度优化.md)

## 一、问题

### 1.1 现象（方案 19 真机验收第 1 项 FAIL）

.25（RK3288，Android 7.1.2）健康推流下，浏览器侧 Playwright 600s 观察
捕获 **15 轮回退**：fallback 日志 30 条、reason 全部为 `timeout`（非
stalled/error/no-stream）、触发时 state=streaming 且帧持续增长（各轮
frameCount 49/102/69/79 与 H 段时长 12.6/26/17/22s 成正比），「正在恢复
画面」角标严格 ~35s 周期出现——**呼吸循环**。同时期后端 0 保活触发、
0 卡死、持续出帧（3.74fps），WS 包计数 H/S 两段均持续收帧：**流前后端
全程健康，纯前端误判**。

### 1.2 根因

`frontend/src/services/videoFallback.ts` `evaluateH264Fallback` 硬兜底
分支（:120）：

```ts
// 硬兜底
if (now - startedAt >= thresholds.HARD_TIMEOUT_MS) {
  return { fallback: true, reason: 'timeout' }
}
```

缺 `state !== 'streaming'` 类守卫。文件头注释（:12）明示该信号原意为
「无论如何不超过 HARD_TIMEOUT_MS **仍未出画面**」，但纯函数化（提交
7138fdd）时丢失了「未出画面」限定；更早的组件内实现是「单一 15s
setTimeout 且进入 streaming 即取消」（:15-16 自述）。

历史脉络：缺陷自 7138fdd 起一直存在（旧阈值 15s 下健康流同样会 15s
后无条件回退），方案 19 阈值联动（提交 f3f16dc）把 HARD 抬到 17s 后
暴露为稳定的 ~35s 呼吸循环；用户最初报告的「时不时会有正在连接出现」
很可能即此缺陷的表现形式之一（与「静止无帧 STALL 误判」并行存在，
方案 19 修了后者、未修前者）。

**呼吸循环机制**：`h264StartedAt`（VideoPlayer.vue:420/276/335 三个
赋值点）每次回切重置 → 健康流从起算点 17s 后必触发 timeout → 回退截图
（suspend 探测）→ 探针 3 窗（6s）达标回切 → 时钟重置 → 下一轮。
闭环自洽的证据：第二轮 H=26s 恰被码率降档的 restarting 宽限
（t+29 → 宽限至 t+44）罩住、宽限结束 0.6s 即回退；第三/四轮 H=17s
精确吻合公式；首轮 12.6s ≈ 17.2s - onMounted 提前 4.6s。

### 1.3 影响

- 体验：健康设备每 35s 一次「推流→回退截图（只读）→恢复」横跳，截图
  期输入失效；
- 干扰判定：.18「0.4fps 故障型」历史判定可能被本缺陷主导（本次验收
  .18 实际 10.16fps 持续出帧，故障链未复现）；
- 带宽/画质：呼吸循环与恢复探针反复触发 request_keyframe 与解码器
  重建，额外 IDR 流量与黑屏抖动。

## 二、修复设计

### 2.1 核心改动（videoFallback.ts:119-122）

```ts
// 硬兜底：仍未出画面（configuring 挂起 / streaming 异常从未出帧）。
// 出过帧后的冻结与卡死已由 stalled 分支全面覆盖（STALL_MS <
// HARD_TIMEOUT_MS 恒成立），健康流不受 HARD 时钟约束
if ((state !== 'streaming' || lastFrameTime === 0) &&
    now - startedAt >= thresholds.HARD_TIMEOUT_MS) {
  return { fallback: true, reason: 'timeout' }
}
```

### 2.2 守卫形式论证（为何不是简单 `state !== 'streaming'`）

`state === 'streaming'` 有两种子情形：

| 子情形 | 现状 | 简单守卫后果 | 选定守卫后果 |
|---|---|---|---|
| 出过帧（lastFrameTime>0）：帧持续 | 17s 后误回退（本缺陷） | 永不回退 ✓ | 永不回退 ✓ |
| 出过帧（lastFrameTime>0）：帧冻结 | stalled 分支先触发（STALL<HARD） | stalled ✓ | stalled ✓ |
| 从未出帧（lastFrameTime=0）：解码器异常 | timeout（现有测试 test.ts:70-77 锁定） | **永不回退，死状态** ✗ | timeout ✓ |

选定守卫同时满足：修复健康流误判、维持 stalled 对冻结的全面覆盖、
保留异常子情形的 timeout 兜底。阈值联动公式
（computeFallbackThresholds：STALL=2×keepalive、NO_STREAM=STALL+2s、
HARD=NO_STREAM+5s）保证三类回退信号语义不重叠、均已覆盖。

### 2.3 边界审计（修复后各场景行为）

| 场景 | 行为 |
|---|---|
| 健康推流（含 .25 静止 3.74fps） | 永不回退；h264StartedAt 时钟走尽无影响 |
| 出帧后冻结（RK3288 编码器崩溃经典故障） | STALL_MS（联动后 10s）触发 stalled，未变 |
| configuring 挂起从未出帧 | 12s no-stream → 17s timeout 双兜底，未变 |
| 回切后（configuring/出帧前）首帧等待 | 17s 上限保留（lastFrameTime=0 走 timeout），未变 |
| 码率重启宽限（restarting） | restartGraceUntil 15s 宽限先于一切判定，未变 |
| error/stopped/idle | 既有分支，未变 |
| 截图模式 h264 suspend（state=stopped） | 不回退（stopped 分支），探针独立运转，未变 |

### 2.4 不改清单

- `h264StartedAt` 三个重置点：每轮 H264 模式重置时钟是设计行为，修复后
  时钟虽继续走但不再触发回退；
- `restartGraceUntil` 宽限、`suspend/resume`、恢复探针（3 窗×2s ≥4 帧）、
  `resumeCooldownUntil` 30s 防横跳冷却、WS 重连接线——修复不触碰；
- 后端全部代码（验证已证后端机制健康）。

## 三、测试（TDD）

先写失败测试，再改实现。

1. **核心锁定（bug 场景）**：`streaming + lastFrameTime>0（帧持续）+
   now - startedAt 远超 HARD_TIMEOUT_MS → 不回退`。现有用例
   「streaming 帧持续到达不回退」（test.ts:52-59）的 now 只到 10.1s，
   未越过 HARD(15s) 边界，故缺陷漏网。
2. **优先级锁定**：`streaming + 帧冻结超 STALL + now 超 HARD → stalled
   优先于 timeout`。
3. **保留语义确认**：现有「streaming 但 lastFrameTime=0（异常）→
   timeout」（test.ts:70-77）与「configuring 长时间挂起 → no-stream/
   timeout」（test.ts:79-84）**必须原样通过**，不改动。
4. 联动阈值路径复测：`computeFallbackThresholds(5000)` 下核心锁定同样
   成立（HARD=17000）。

实施流程照项目规范：失败测试先行 → 最小修复 → 全量门禁
（vitest/eslint/build；本修复不触后端，pytest 基线不回归）。

## 四、真机验收（修复后，复用方案 19 验证基建）

后端已在运行新代码，前端 dev server 就绪，设备 .25/.18 可达。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 1 | Playwright `stream-stability-19.spec.ts` 观察模式 .25（600s） | `fallbackLogs == []`（此前 30 条）；无 35s 周期角标；帧计数单调增长 |
| 2 | 同 spec .18（600s） | 呼吸循环消失；若仍回退须为 stalled（真实断流）且无死循环，时间线人工判定 |
| 3 | 卡死注入回归（STOP CodecLooper） | stalled→探针→重启→恢复链不受影响（呼吸循环消失后 stalled 是唯一回退源） |
| 4 | WS 瞬断回归 | 6a/6b 路径不变（瞬断回退截图 + 退避重连回切） |
| 5 | .18「0.4fps 故障型」复检 | 长时间观察出帧行为，判定此前结论是否被本缺陷干扰 |

## 五、走查调研：现有机制优化空间（本次代码走查 + 真机实测结论）

### 5.1 O6 自适应码率对静止设备误判降档（真实缺陷候选，建议另立方案 21）

**现象（本次真机实测）**：.25 静止画面 3.74fps 持续上报（前端
`startH264StatsReporting` 仅 fps≥1 上报）→ `BitrateAdvisor`
（bitrate_advisor.py）target_fps=30、bad_line=21fps：3.74fps 全为坏样本
→ 每 30s 冷却降一档，实测 4M→2M→1M 两次连带编码器重启（各 1-3s 黑屏 +
WS 解析器 epoch reset）。

**缺陷双重**：
1. **误判**：帧率受内容驱动（静止画面帧少是正常的），决策器无信号区分
   「内容静止」与「链路拥塞」；
2. **不回弹**：升档条件为窗口内 good≥8 连续好样本——静止设备 fps 永远
   低，降档后无改善也不会升回 → **该设备在本次连接生命周期内永久停在
   最低码率档**（连接时长越久画质越差，重连才复位）。

**优化方向候选**（择一或组合，另立方案评估）：
- a) 无效降档回弹：降档后冷却窗 + 观察窗内 fps 改善 <阈值（如 10%）判
  「降档无效」→ 升回原档 + 该连接冷静期；
- b) 前端上报扩展：stats 消息增加 droppedFrames（前端已统计
  `H264StreamStats.droppedFrames`，仅未上报）或帧间隔抖动，「帧持续
  到达且零丢帧」判为健康不降档；
- c) 静止期冻结决策：画面静止判定（帧尺寸/关键帧周期特征）期间暂停
  决策器采样。

### 5.2 O2 重连竞争窗（6c 偶发拒绝，维持现状裁定，不做改动）

现状：旧流 teardown（stop 置 False → 保活等待轮回退出 ≤2s + process
wait 2s 等）与 3×1s 收尾窗交叠时，新流 adb forward 被拒一次（本次实测
gap=1 必现）。前端指数退避 1s 后重试即恢复，已实测通过。终审已裁定
「拒绝偶发属预期」。

本轮调研再评估：纯改动收益 = 消除 40% 概率的一次 1s 延迟；代价 = 动
stop 链路（等保活帧换即时断开，牺牲优雅收尾）或拉长等待窗（把延迟从
40%×1s 变成 100%×N s，负优化）或引入条件等待（复杂度）。**维持现状，
记入观察项**；若未来真实环境中该拒绝率明显升高再评估。

### 5.3 O3/O4/O5 走查结论（无缺陷，不做）

- **O3 保活在 .25/.18 未触发**：两台设备静态画面仍持续产帧（.25 状态栏
  时钟 3.74fps、.18 近空帧 10.16fps），空闲不足 5s。保活触发链已由卡死
  注入实测证实（注入后 6.0s 发 RESET）。设计正确，非缺陷。
- **O4 watchdog 驱动**：checkH264Fallback 由 h264VideoStream 的 1s fps
  定时器经 onStatsUpdate 周期驱动，非纯事件驱动——「无帧无事件则不判
  定」的担心不成立。
- **O5 request_keyframe 防抖窗口**：`_last_keyframe_at` 随流生命周期
  清理（终审裁定自愈安全）；跨流交叠窗口使防抖失效为无害小事
  （多一次 RESET 而已），不做。

### 5.4 O7 .18 历史判定复检（并入本方案验收）

方案 19 验收发现 .18「0.4fps 故障型」历史判定与本次 10.16fps 实测矛盾，
疑似被 timeout 呼吸循环干扰。本方案修复后按第四节第 5 步复检，若有
新结论回写相关文档。

## 六、实施范围

- **本方案（20）**：第二节修复 + 第三节测试 + 第四节真机验收；规模
  单文件纯函数改动 + 测试追加，TDD 两步内完成，不做实施计划文档拆分；
- **另立（21 候选）**：5.1 自适应码率静止误判（决策器 + 上报协议扩展，
  中等规模）；
- **不做**：5.2/5.3 已裁定维持现状。