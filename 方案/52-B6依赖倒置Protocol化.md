# 52 - B6 依赖倒置 Protocol 化（方案 51 缓批项立项）

> 状态：**已实施并随 v0.7.14 正式发布**（2026-10-22 用户裁决 D1 扩面六处一并治理、D2-D5 按默认后，当日
> C1-C6 六 commit 落地：编码器链按 §四方案 A、扩面按 §四之二 R1-R5，零运行时逻辑改动。
> 门禁六件套全绿 + 真机抽验两判据 PASS，详见 §六之补与更新记录）
> 依据：方案 51 §四 T3 拍板记录「B6 缓至下批」；走查条目 `docs/reviews/2026-10-20-代码走查与二次调研.md` B6（定性明确档）
> 基线：v0.7.13 + 方案 51 批次（master `db3ccdc`）。流程遵守 DEVELOPMENT.md 约束 7：调研与根因在本文档完成，拍板后实施。

## 一、背景与问题定性

方案 51 走查 B6：**应用层 `stream_service.py` 直接 import 基础设施具体类
`ScrcpyEncoder`/`EncoderStalledError`（`app.infrastructure.stream.scrcpy`），
违反四层依赖倒置**——DEVELOPMENT.md 约束 2/3 原文：

- 「Application 层**不依赖**具体实现（依赖 Protocol）」（:76）
- 「**禁止**：在 Application/Domain 层直接 import 具体实现类」（:87）
- 「所有外部依赖（ADB、数据库、**编码器**）必须通过 Protocol 定义，依赖注入通过 deps.py 集中管理」（:83-84）

且 `stream_service.py` 模块 docstring 自称「依赖（通过 Protocol 注入）：VideoEncoder」
（:20-21）——**声明与实现不符**，这是走查「与设计不符」定性的核心。

当时缓批理由：纯架构重构、风险面在推流主链（P0 链路），不与 112 项功能修复混批。

## 二、现状调研（2026-10-22）

### 2.1 依赖边清单（stream_service → infrastructure 共 4 条）

| 位置 | 引用 | 性质 |
|---|---|---|
| `stream_service.py:38` | `from app.infrastructure.stream.scrcpy import EncoderStalledError, ScrcpyEncoder` | **违规边**（模块级 import） |
| `:77` | `self.encoders: dict[str, ScrcpyEncoder]` | 类型标注用具体类 |
| `:78` | `self._encoder_factory = encoder_factory or ScrcpyEncoder` | **默认工厂引用具体类**（违规边落点） |
| `:194/233/268` | `EncoderStalledError` 捕获/上抛 | 异常类引用 |
| `:443` | `get_encoder() -> ScrcpyEncoder \| None` | **具体类型外溢到接口层**（video.py 经此取用） |

### 2.2 编码器真实调用面（Protocol 应钉的契约）

应用层 + 接口层实际消费的全部成员：

| 成员 | 调用方 | 现有 `VideoEncoder` Protocol（ports.py:646）是否覆盖 |
|---|---|---|
| `start(device_id, opts) -> AsyncIterator[tuple[int, bytes]]` | stream_service:198 | ✗ **签名陈旧**：声明 `AsyncIterator[bytes]`，方案 32 加 PTS 后实现已是 `tuple[int, bytes]` |
| `stop()` | stream_service:242 | ✓ |
| `request_keyframe() -> None` | stream_service:382 | ✗ 缺失 |
| `send_input(data: dict[str, Any]) -> None` | ws/video.py:492 | ✗ 缺失 |
| `codec` 属性 | ws/video.py:233 | ✗ 缺失 |
| `resolution` 属性 | ws/video.py:380/491 | ✗ 缺失 |

**关键事实：现有 Protocol 从未被任何代码用作类型标注，mypy 从未校验过
`ScrcpyEncoder` 与它的一致性——所以 `start` 返回签名早已漂移而无人发现。**
这既是现状的尴尬，也是 Protocol 化后立刻获得的收益（一致性进编译期）。

### 2.3 相关现状约束

- `deps.py:152` `get_stream_service()` 现为 `StreamService()`（无参，默认工厂内置）；
  deps 组合根已 import 基础设施（AdbCliDriver、Sqlite* 仓库），补一个 ScrcpyEncoder 符合同位职责。
- `scrcpy.py` 已 import `app.core.exceptions`（ScrcpyOccupiedError）与
  `app.domain.ports`（EncoderOpts）——异常下沉到 core 与该文件既有 import 方向一致，无循环。
- `ws/video.py` **不 import** 任何 infra（grep 证实），只经 `get_encoder()` 返回值 duck-typing；
  返回类型改 Protocol 后接口层零改动。
- 无架构 lint/import 契约测试强制执行分层（全仓无 import-linter、无守卫测试），
  违规目前只存在于文档口径与人工走查。
- 测试直依：`EncoderStalledError` 被 `tests/scrcpy/test_scrcpy_encoder.py:1210/1278/1697`、
  `tests/application/test_stream_adaptive.py:21/243` 从 infra 路径 import；
  `StreamService()` 无参构造在 `tests/application/test_stream_keyframe.py:62/81/93`、
  `tests/manual/spike_task_leak.py:47` 共 4 处。mypy 门禁仅覆盖 `backend/app`，测试文件不受类型检查。

### 2.4 同型违规面（界定本期范围用，不在本期动）

应用层 import infra 具体类共 **6 处文件**，B6 只点名了 stream_service：

| 文件 | 直依类 |
|---|---|
| `stream_service.py:38` | ScrcpyEncoder/EncoderStalledError（**B6 本项**） |
| `debug_service.py:53-54` | BatchLogWriter、SqliteMetricsRepository |
| `performance_service.py:25-27` | PerformanceSampler、MetricRecorder、SqliteMetricsRepository |
| `network_service.py:26-32` | sampler 族、MetricRecorder、SqliteMetricsRepository |
| `recording_support.py:30` | MetricRecorder |

其余 5 处走查未立项（无对应 Protocol：MetricsRepository/BatchWriter/Sampler 均不在
ports.py 现有四 Protocol 之列），治理它们需要另行定义 3-4 个新端口，改动面与
拍板量级远超 B6，**本期不扩面**。

## 三、根因

1. 分层规则无机器强制（无 lint 契约），Protocol「定义即终点」而非「使用即终点」。
2. 演进顺序倒置：先有 ScrcpyEncoder 再补 Protocol 文本，`VideoEncoder` 补写时按
   早期设想建模（bytes 裸流），从未回填真实面，遂成「自称 Protocol、实用具体类」。
3. 异常作为流程控制信号被放在了实现类模块里（stall 自愈逻辑属应用层编排语义，
   其信号类型却住在基础设施层），使应用层 import infra 的边里混入了「不得不断」的一条。

## 四、方案设计（推荐方案 A）

**纯类型/位置搬迁，零运行时逻辑改动**——B1 在飞任务复用、R2 identity 守卫、
卡死自愈滑窗等推流主链逻辑一字不动。

### A1 异常下沉（2 文件）

- `EncoderStalledError` 类体（含 `reason` 关键字参数）原样迁至 `core/exceptions.py`
  独立段（**不入 OpenScrcpyException 树**——它非 HTTP 映射异常，现基类 RuntimeError 保持；
  在文件 docstring 异常树之外加注「非 HTTP 域内异常」防误读）。
- `scrcpy.py` 删本地定义、改从 core import（该文件已有同路径 import 先例）；
  raise/except 行为零变化。
- 测试 5 处 import 路径机械改新址（tests 无 mypy 约束但保持路径真实）。

### A2 Protocol 回填真实面（1 文件）

`domain/ports.py` `VideoEncoder` 扩为 §2.2 六成员：

```python
@runtime_checkable
class VideoEncoder(Protocol):
    @property
    def codec(self) -> str: ...
    @property
    def resolution(self) -> tuple[int, int]: ...
    def start(self, device_id: str, opts: EncoderOpts) -> AsyncIterator[tuple[int, bytes]]: ...
    async def stop(self) -> None: ...
    async def send_input(self, data: dict[str, Any]) -> None: ...
    async def request_keyframe(self) -> None: ...
```

- **修正 `start` 返回签名漂移**（bytes → tuple[int, bytes]，对齐方案 32 PTS 透传现实）。
- **实施勘误（C2 mypy 首校验实证）**：`start` 声明为**同步 `def` 返回
  `AsyncIterator`**而非成稿样例的 `async def`——实现是 async generator function，
  mypy 视 AGF 为 `def ... -> AsyncIterator`；写成 `async def` 会被建模成协程函数、
  调用得 Coroutine 不可 `__anext__`。本节样例此处本就踩中 §2.2「Protocol 从未被校验」
  的同一坑，实证于 `ports.py:672` 的 docstring 记档。
- docstring「未来实现：MediaCodec/FFmpeg」保留；补一句契约语义（request_keyframe
  无通道时 no-op 等，从实现 docstring 提炼，不新增约束）。

### A3 应用层断边（1 文件）

`stream_service.py`：

- 删 :38 infra import；`EncoderStalledError` 改自 core，`VideoEncoder` 自 domain.ports。
- :62 `encoder_factory: Callable[[], VideoEncoder]`（**必填，删 `= None` 与
  `or ScrcpyEncoder` 默认**——默认值内藏具体类正是违规落点；fail-fast 优于运行到
  首个起流才 `None()` 崩）。
- :77 `dict[str, VideoEncoder]`；:443 `get_encoder() -> VideoEncoder | None`。
- docstring :20-21 声明自此与实现一致。

### A4 组合根收口（1 文件，**修正方案 51「deps 不动」口径**——见 D3）

`deps.py`：`get_stream_service()` 改
`StreamService(encoder_factory=ScrcpyEncoder)`，补 import。
工厂类型 `type[ScrcpyEncoder]` 的 `__init__(clock=…)` 有默认值，满足
`Callable[[], VideoEncoder]`；mypy 在此点首次做结构一致性校验。

### A5 测试构造点回填（2 文件）

`test_stream_keyframe.py` 3 处、spike 1 处（manual 顺手改）补
`encoder_factory=ScrcpyEncoder`。

### 改动量汇总

**6 文件（app 4 + tests 2，另 manual 1 行）≈ 60-80 行 diff**，其中运行时
可执行语句的净变化仅 2 条（StreamService 构造签名、deps 传参）；其余为
import 路径与类型标注。

### 否决的替代方案

| 方案 | 内容 | 否决理由 |
|---|---|---|
| B 惰性函数内 import | `__init__` 里局部 `from app.infrastructure…` | 依赖边仍在（运行时/静态均可查出），仅躲开「模块级 import」字面检查——假治理，违背 DEVELOPMENT.md:83 精神 |
| C 改文档口径 | 在 DEVELOPMENT.md 接受「应用层默认可直依 infra」 | 与 CLAUDE.md「严格四层、依赖倒置通过 Protocol」硬约束直接冲突；收益为零 |
| D 六处一起改 | B6 + 其余 5 处同批 | 成稿时按「需新立 3-4 端口、拍板面数倍放大」否入 D1 默认；**2026-10-22 用户拍板改判为做**，且复研发现可走「搬正错位层位」而非造端口——扩面设计见 §四之二 |

## 四之二、扩面设计（D1 拍板：六处一并治理，2026-10-22 补调研后定稿）

拍板后复研（本轮 §2.4 之外的新事实）：其余五处直依类的**全部依赖只有端口与数据**——

| 类 | infra 技术使用 | 实际依赖 |
|---|---|---|
| PerformanceSampler | **无** | core + `AdbDriver` 端口 + domain.runtime |
| NetworkSampler | **无** | core + `AdbDriver` 端口 |
| MetricRecorder | **无**（经注入 repo 端口落库） | core + `MetricsRepository` 端口 + 两 dataclass |
| BatchLogWriter | **无**（经 `DebugRepository.save_logs_bulk`） | core + `DebugRepository` 端口 + LogEntry |
| PerformanceMetrics / NetworkStats / NetworkConnection | 纯 dataclass | stdlib + domain.runtime |

即其违规实质是**层位放错**（把纯逻辑件放进 infra 目录），不是缺抽象。

**Ruling R1（机制分治）**：对「真 infra 适配器」用 Protocol 化（方案 A）；对「放错层的纯逻辑件」用**层位搬正**——不替零替换面的单实现类造 Protocol 假抽象（那是接口债务不是治理）。判据：该类是否亲触子进程/socket/驱动等基础设施技术。`ScrcpyEncoder` 是（subprocess/adb forward/socket）→ Protocol；上述四件不是 → 搬正。

### R2 三 dataclass → `domain/runtime.py`

同文件既有 BatteryInfo/CpuProcessInfo「dumpsys 解析产物数据对象」同族安家（其 docstring 自陈）。
PerformanceMetrics / NetworkStats / NetworkConnection 平移，全仓引用路径随改。

### R3 四纯逻辑类 → 应用层（git mv + 路径改，实现零改动）

| 旧址 | 新址 |
|---|---|
| `infrastructure/performance/sampler.py` | `application/performance_sampler.py` |
| `infrastructure/network/sampler.py` | `application/network_sampler.py` |
| `infrastructure/persistence/metric_recorder.py` | `application/metric_recorder.py` |
| `infrastructure/persistence/batch_writer.py` | `application/batch_writer.py` |

搬后 debug/performance/network/recording_support 对它们的 import 属应用层 intra 合法；
docstring 层注改「应用层」；`infrastructure/performance|network` 两空子包删除；
`infrastructure/__init__.py`、`persistence` 包说明同步勘正；`app.scrcpy/__init__.py:11`
注释「ScrcpyEncoder 位于 infrastructure」仍真（不动）。

### R4 `SqliteMetricsRepository` 默认值 → 必填参数 + deps 注入（D3 同款）

Debug/Performance/NetworkService 三处 `metrics_repo or SqliteMetricsRepository()` 默认是违规落点；
端口 `MetricsRepository` 本已存在、参数名不变、语义不变，仅：必填化（去默认）+
deps `@lru_cache get_metrics_repository() -> MetricsRepository`（无状态仓库单例，
与 device/debug repo 同款 lru_cache 理由）供三服务注入。

### R5 测试构造点与路径回填

- metrics_repo 必填化波及：DebugService 63 处、PerformanceService 14 处、NetworkService 17 处
  （tests 不受 mypy 约束，构造时补 `metrics_repo=SqliteMetricsRepository()`——与现默认实例化行为
  逐点等价，不新增替身）。
- sampler/recorder/writer/dataclass 搬址波及 import 路径：~15 处测试文件（含
  test_sampler/test_perf_sampler/test_network_sampler/test_batch_writer/test_http_network/
  test_perf_recording/test_network_service/test_performance_service 等）。
- encoder 链波及（§A5）：StreamService 4 处构造 + EncoderStalledError 5 处路径。

### 扩面后改动量与风险补充

总规模约 **app 9 文件 + deps 1 + tests ~25 文件，净 diff 数百行**，其中**运行时逻辑改动仍为零**
（类体一字不动；可执行面净变化 = StreamService/metrics_repo 构造签名 4 处 + deps 装配 4 处）。
新增风险面：搬址穷尽性（grep 清单 §2.4/§2.3 + ImportError 首跑全暴露，漏改不可能静默存活）；
测试大面回填的机械错误（以全量 pytest 为闸）。原「造 4 端口 + 工厂参数」假想路线被 R1
替代后，**省约 150 行端口/工厂 plumbing 与 60 处替身构造改造**。

## 五、风险评估与收益评估（用户问的「综合考量」）

### 5.1 风险（对现有改动是否过大 / 会不会伤功能）

| 维度 | 评估 |
|---|---|
| 改动面 | **小**：6-7 文件 ~70 行，无逻辑分支改写、无协议/数据格式变化、无前端参与 |
| 运行时行为 | 出厂包等价：同一 `ScrcpyEncoder` 实例、同一异常类（仅换定义模块）、同一调用序；deps 组合根的 import 清单 +1 行 |
| 主要风险点 1 | **异常类单一身份**：迁移后若残留旧 import 路径形成两个类对象，`except` 漏接→stall 自愈失效退化为杀流。缓解：全仓 grep 钉死引用点（生产 4 处 + 测试 5 处，清单见 §2.3），删源不留 re-export（禁兼容垫片） |
| 主要风险点 2 | **mypy 首校验暴露既有漂移**：Protocol 修正后若实现仍有签名不符（如 `start` 泛型细节）会红。缓解：这正是收益；实施首步先跑一致性门禁再铺开标注改动 |
| 主要风险点 3 | 工厂必填化改变公开构造签名：仓内调用点共 6 处（§2.3）全部同批改；无外部消费者（exe 单仓形态） |
| 回归防线 | 既有大测试面兜底：`test_scrcpy_encoder.py`（~1700 行全生命周期）、`test_stream_adaptive.py`（stall 自愈/重启预算/22 处工厂注入）、`test_stream_keyframe.py`、`test_video.py`（FakeEncoder 30+ 用例）+ 门禁三件套 + 真机抽验一条（推流 1 帧 + 输入回显 + request_keyframe，同方案 51 F1-01 判据） |
| 回滚 | 单 commit 序列 revert 即可，无数据/接口/配置迁移 |

综合：**改动不大、风险中低且全部可被既有测试与一次真机抽验覆盖**。「风险面在
推流主链」的判断成立于「逻辑要动」的前提；本方案逻辑零改动，风险实质是
「搬迁完整性」问题，用 grep 清单 + mypy + 测试三重复核可穷尽。

### 5.2 收益与后续升级价值

| 收益 | 具体性 |
|---|---|
| **合规闭环**（主要） | 走查 B6「与设计不符」销账；CLAUDE.md/DEVELOPMENT.md 硬约束在编码器这条最核心外部依赖上首次真实成立；stream_service docstring 自称与实现一致 |
| **接口层去泄漏** | `get_encoder()` 不再把 infra 类型递给 ws 层；将来替换编码器时 ws 侧零 import 变化 |
| **契约进编译期**（升级价值核心） | mypy 自此校验 ScrcpyEncoder≡VideoEncoder。§2.2 已证明：未校验的 Protocol 会漂移（start 签名烂了半年无人知）；钉上之后，**方案 45 codec 放开 h265 类的演进已暴露「WS 层需要 codec/resolution 契约」的需求**，未来换真实第二编码器（协议文本预留的 MediaCodec/FFmpeg）或给推流写更细的假编码器单测，都获得类型锚 |
| **测试 fake 升格** | 22 处工厂注入的 fake 自此有了明确的契约靶（可加 `isinstance` 断言或标注 VideoEncoder） |
| **不做的代价** | B6 永久挂账；且随功能迭代「应用层 import infra」会像其余 5 处一样继续生长（走查下一轮必然复发同类项，还债成本递增） |

收益诚实评估：**无直接用户可见收益、无性能收益**；属架构还债 + 升级使能
（enabling）型收益。价值高低取决于「是否认真执行四层约束」——该约束是
CLAUDE.md 白纸黑字，若答案是否（接受现状），则应改约束文本而非放任文档与代码矛盾。

## 六、实施要点（拍板后 · 扩面版）

单批次预计 6 个 commit（每个独立过全量 pytest，搬址漏改会以 ImportError 当场暴露）：

1. `refactor(core)`: A1 `EncoderStalledError` 下沉（core 定义 + scrcpy/stream_service import + tests 5 处路径）——先行使 mypy 可独立验证异常无分裂。
2. `refactor(domain+app+deps)`: A2-A5 编码器链 Protocol 化（VideoEncoder 六成员 + start 签名修正 + stream_service 断边/工厂必填 + deps 传工厂 + StreamService 4 测试构造点）。
3. `refactor(domain)`: R2 三 dataclass 入 `domain/runtime.py`，sampler/recorder/app/tests 引用路径随改。
4. `refactor(app)`: R3 四纯逻辑类搬入应用层 + 空子包删除 + 包说明/层注勘正 + tests ~15 处 import 路径。
5. `refactor(app+deps)`: R4 metrics_repo 必填化 + deps `get_metrics_repository()` 单例 + tests ~94 处构造点回填。
6. `docs`: 方案 52 收口 + 方案 51 勘误（D3 相关）+ 进度追踪第一百一十一次。

门禁：后端三件套全量 + 前端三件套（无前端改动，按当期门禁惯例仍跑一遍收口）。
真机抽验：推流 1 帧 + 触控 send_input + request_keyframe 回切 + stall 自愈观察
（=方案 51 F1-01 判据，覆盖搬移的编码器链）；performance/network 页监控启动→采样滚动→
录制 start/status/stop 落库行数回显（覆盖 R2/R3/R4 搬移件活链）。

### 六之补、实施结果（2026-10-22 收口）

| commit | 内容 | hash |
|---|---|---|
| C1 | A1 异常下沉（core 定义 + scrcpy/stream_service import + tests 5 处路径） | `589ea30` |
| C2 | A2-A5 编码器链 Protocol 化（六成员 + AGF 签名修正 + 断边/工厂必填 + deps 传工厂 + 4 测试构造点） | `e72e50b` |
| C3 | R2 三 dataclass 入 `domain/runtime.py` + 引用路径 | `1c42c30` |
| C4 | R3 四纯逻辑类 git mv 搬应用层 + 空子包删除 + 层注/包说明 + tests 路径 | `9016d41` |
| C5 | R4 metrics_repo 必填化 + deps 单例注入 + tests 97 处构造回填（预估 ~94，实际含多行/子类构造 4 处修正） | `01ce720` |
| C6 | docs 收口（本勘误 + 方案 51 B6 行勘误 + 进度追踪一百一十一次） | 本提交 |

- **门禁（2026-10-22 全绿）**：后端 ruff / mypy strict（77 文件，Protocol 首校验点）/
  pytest **1355 passed, 1 skipped, cov 93.87%**（与方案 51 基线一致零增减）；
  前端 vitest **728/728** / eslint 全量 / vue-tsc。
- **真机抽验（.18，8766 隔离实例 + 5039 独立 adb，判据全 PASS）**：
  - 编码器链（F1-01 同款）：推流 11 帧 + config + IDR×3；`send_input`（touch + HOME key）
    后持续出帧零 error；`request_keyframe` 后 IDR 再现（第 4 个）；后端日志 0 stall/Traceback。
    stall 自愈路径真机不可主动注入，由 `test_stream_adaptive` 5 项钉 + 被动观察窗未见异常。
  - perf/network 录制链：临时置 `metrics.recording=true`（user.yaml + config/reload 热生效）→
    perf 监控 start(1s) → 录制 status rows 0→9→12 递增 → stop 回显 **rows=17**；
    network stats 冷启动拉采样(2s) → 录制 rows 0→4→6 → stop 回显 **rows=6**；
    删 user.yaml 还原重载后 record/start 复归 400 RECORDING_DISABLED（开关路径同场坐实）。

## 七、拍板记录（2026-10-22 用户裁决）

| # | 问题 | 裁决 |
|---|---|---|
| D1 | 本期范围：仅 B6 编码器链，还是扩为六处全治？ | **「把其余5处一并处理了」**——扩面全治；机制按 §四之二 R1-R5 复研修正版（层位搬正为主，Protocol 化仅用于 ScrcpyEncoder） |
| D2 | `EncoderStalledError` 落点 | **core/exceptions.py 独立段**（按默认） |
| D3 | deps 传工厂 + 参数必填（方案 51「deps 不动」作废记勘误） | **按默认确认** |
| D4 | `VideoEncoder` 六成员面 | **按默认确认可** |
| D5 | 独立小批先行（不与功能批混走） | **按默认独立成批** |

## 更新记录

- 2026-10-22：立项调研成稿（依赖边/真实调用面/Protocol 漂移/测试与构造点全清单、
  方案 A 设计与 B/C/D 否决理由、风险与收益评估、5 项拍板清单）。待拍板。
- 2026-10-22：用户拍板——D1 改判扩面六处一并治理，D2-D5 按默认。拍板后复研新事实：
  其余五处直依类零 infra 技术（只依端口），实质为层位放错；据此定 §四之二
  Ruling R1（机制分治：真适配器 Protocol 化 / 错层纯逻辑件层位搬正）与 R2-R5 设计，
  §六 commit 划分改 6 步、§五 否方案 D 行注记改判、§七 转拍板记录。进入实施。
- 2026-10-22：**C1-C6 实施完成，状态转「已实施」**。全程零运行时逻辑改动；
  mypy 在 C2 首次校验即暴露本方案 §A2 样例自身的 AGF 建模缺陷（`async def start`
  应为 `def start -> AsyncIterator`），已随实施修正并回写样例（§四 A2 勘误注）。
  C5 实测构造点 97 处（预估 ~94，差 3 处为多行 DebugService 与子类构造）。
  门禁六件套全绿、真机抽验两判据 PASS，明细见 §六之补。C1-C5 本地提交未 push（待授权）；
  方案 51 B6 行「deps 不动」口径随 C6 勘误。
