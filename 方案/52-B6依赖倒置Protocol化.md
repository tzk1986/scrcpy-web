# 52 - B6 依赖倒置 Protocol 化（方案 51 缓批项立项）

> 状态：**待拍板**（2026-10-22 立项调研完成，风险评估与收益评估见 §四/§五，拍板清单见 §七）
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
    async def start(self, device_id: str, opts: EncoderOpts) -> AsyncIterator[tuple[int, bytes]]: ...
    async def stop(self) -> None: ...
    async def send_input(self, data: dict[str, Any]) -> None: ...
    async def request_keyframe(self) -> None: ...
```

- **修正 `start` 返回签名漂移**（bytes → tuple[int, bytes]，对齐方案 32 PTS 透传现实）。
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
| D 六处一起改 | B6 + 其余 5 处同批 | 需新立 MetricsRepository/BatchWriter/Sampler 等 3-4 端口，拍板面与回归面数倍放大，且走查未立项=无既有裁决授权扩面 |

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

## 六、实施要点（拍板后）

单批次预计 3 个 commit：

1. `refactor(core)`: A1 异常下沉（core + scrcpy import 点 + tests 5 处路径）——先行使 mypy 可独立验证异常无分裂。
2. `refactor(domain+app)`: A2+A3 Protocol 回填 + 应用层断边（含 start 签名修正）——此 commit 起 mypy 首校验一致性。
3. `refactor(deps)+test`: A4+A5 组合根与构造点回填；收尾 `test_video.py` FakeEncoder 标注（可选加 1 条结构 isinstance 钉）。

门禁：后端三件套全量 + 前端三件套（无前端改动可跳过，按当期门禁惯例仍跑一遍收口）
+ 真机抽验（.18 或 .22：起流 1 帧、触控 send_input 生效、request_keyframe 回切、
拔线触发 stall 自愈至预算上抛 error 文案）。

## 七、拍板清单（待用户确认）

| # | 问题 | 默认建议 |
|---|---|---|
| D1 | 本期范围：仅 B6 编码器链（其余 5 处同型违规登记低优先池另行立项），还是扩为六处全治？ | **仅 B6**；其余 5 处记入路线图低优先池（新立「应用层 infra 直依全量治理」条目） |
| D2 | `EncoderStalledError` 落点：`core/exceptions.py` 独立段（方案 51 默认，非 HTTP 树），还是 `domain/ports.py` 随 Protocol 放？ | **core/exceptions.py**（与 ScrcpyOccupiedError 等既有跨层异常同址；ports 保持纯端口+数据类） |
| D3 | 工厂注入点口径：方案 51 原文「注入点 deps 不动」与「应用层断边」互斥（默认工厂内藏具体类即断不净）。是否确认修正为 **deps 传 `encoder_factory=ScrcpyEncoder` + StreamService 参数必填**？ | **确认修正**（deps 本就是组合根，其余服务同款；「deps 不动」文本随本方案作废并在方案 51 更新记录记勘误） |
| D4 | `VideoEncoder` Protocol 成员面按 §2.2 六成员（含 codec/resolution 属性）钉，是否可？（未来实现须同时提供这四方法两属性） | **可**（即 WS/应用真实消费面，不多不少；runtime_checkable 保留） |
| D5 | 实施排期：本项独立成批（3 commit，半天级），还是与下一功能批合并走？ | **独立小批先行**——改动干净、风险窗口短，不背功能批的回归包袱 |

## 更新记录

- 2026-10-22：立项调研成稿（依赖边/真实调用面/Protocol 漂移/测试与构造点全清单、
  方案 A 设计与 B/C/D 否决理由、风险与收益评估、5 项拍板清单）。待拍板。
