# 性能阈值告警实施计划（方案 24）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按方案 24 实现性能阈值告警——后端纯逻辑评估器（连续判定 + 迟滞 + 冷却）挂接两路采样循环，复用既有 WS/轮询通道内嵌下发 alerts 快照，前端 toast + 徽标提示；实施完成后重建 Windows 绿色版 zip 并运行时验证可用。

**Architecture:** 遵循四层架构——`AlertEngine` 为应用层纯逻辑状态机（`bitrate_advisor.py` 同层先例）；`PerformanceService` / `NetworkService` 采样循环内先评估后推送；接口层薄适配（WS perf 消息与 HTTP network stats 响应各加 `alerts` 键）；前端 `src/services/alerts.ts` 共享模块（防御式解析 + episode 去重通知）。告警**不落库**（不进 MetricRecorder/导出/历史 API）。

**Tech Stack:** Python 3.10+ / FastAPI / pydantic v2 BaseSettings / pytest / Vue 3 + Element Plus / vitest。

**Spec:** `方案/24-性能阈值告警方案.md`（§2 决策 D1-D7、§3 配置、§4 后端设计、§5 前端设计、§6 测试策略、§7 验收标准）

## Global Constraints

- 提交信息用中文，结尾加 `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`；执行于 master 直接提交（方案 23 Ruling 1 先例）；**不推送**（需用户显式授权）。
- 零新依赖、打包体积不变（无新第三方包）。
- 测试位置：后端一律 `tests/`（`tests/application|infrastructure|unit`）；前端与源文件同目录 `*.test.ts`。`vitest.config.ts` 对 `src/services/**` 有 80% statements/lines 覆盖率硬阈值——`alerts.ts` 测试必须过线。
- 门禁（每任务完成前自跑；T4 全量复跑）：
  - `python -m ruff check backend/app`
  - `python -m mypy backend/app`（strict）
  - `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80`
  - `cd frontend && npm run lint && npx vitest run --coverage && npm run build`
- 告警不落库：不改 `MetricRecorder`、不改导出/历史 API；`fps_fresh` 字段不进 `_metrics_to_dict`、不进 WS 消息、不进落库元组。
- 默认阈值与既有硬编码警示色对齐（cpu above=80、fps below=30、内存 above=90）——零告警时 UI 肉眼与现状一致。
- 不动运行中的 dev 后端（0.0.0.0:8765）；打包验证时 exe 应回退 8766 与 dev 共存。
- 通知书（`方案/24-性能阈值告警方案.md` §6）列出的两处既有精确断言会被接口加键打破——属**预期更新**，不是意外失败：
  - `tests/unit/test_http_network.py::test_stats_snapshot_fields`（全字典相等）
  - `tests/unit/test_metrics_config.py::test_base_yaml_has_metrics_section`（metrics 节全字典相等）

## Review Focus

以下输入/失败模式是方案隐含但最可能被实现者做错的点，各自有测试钉在对应任务里：

1. **fps 无效样本不得触碰任何计数器**：`fresh=False`（沿用轮）与 `value=None`（首轮/解析失败）两条路径都必须整条跳过——不算越限、不算安全、不重置已累积的 breach_count（T1 测试 4；T2 采样器标记）。
2. **notify 是窗口匹配而非一次性消费**：`notify = notified_ts is not None and 0 <= sample_ts - notified_ts <= notify_window`——冷却内触发为 `notify=False` 但照常进徽标（T1 测试 1/3）。
3. **规则热重载 `sync()`**：移除/禁用丢状态（徽标同步消失）、参数变化计数重置、新增从零起（T1 测试 6）。
4. **引擎释放三条路径**：perf 走 `_release_device` 单点；network 没有该单点，须在 `_sampling_loop` 的 `finally` 与 `stop_monitoring` **两处**都 pop（T2 测试）。
5. **前端防御式**：既有 PerfView 测试推送的消息无 `alerts` 键，`extractAlerts(undefined) === []` 必须成立且不弹 toast（T3 测试）。

---

### Task 1: 配置模型与告警评估器

**Files:**
- Modify: `config/settings.py`（`MetricsConfig` 之前新增两个模型；`MetricsConfig` 加 `thresholds` 字段）
- Modify: `config/base.yaml`（metrics 节尾部加 `thresholds`）
- Modify: `tests/unit/test_metrics_config.py`（两个精确断言同步 + 新增覆盖解析用例）
- Create: `backend/app/application/alert_engine.py`
- Create: `tests/application/test_alert_engine.py`

**Interfaces:**
- Consumes: `config.settings.settings()`（热重载单例）、`app.core.logging.get_logger`。
- Produces（Task 2 依赖）:
  - `AlertRule`（frozen dataclass：`id, above, below, consecutive, clear_margin, clear_consecutive, cooldown`）
  - `AlertEngine(notify_window: float)`，方法 `sync(rules: list[AlertRule]) -> None`、`evaluate(metric_id: str, value: float | None, ts: float, fresh: bool = True) -> None`、`snapshot(sample_ts: float) -> list[dict[str, Any]]`
  - `build_rules(metric_ids: tuple[str, ...] = METRIC_IDS) -> list[AlertRule]`（只产出 enabled 且方向合法的规则）
  - `settings().metrics.thresholds.<metric_id>` → `AlertRuleConfig`（`enabled/above/below/consecutive/clear_margin/clear_consecutive/cooldown`）

- [ ] **Step 1: 扩展 settings 模型**

在 `config/settings.py` 的 `class MetricsConfig` **之前**插入两个新模型（紧跟 `DebugConfig` 之后）：

```python
class AlertRuleConfig(BaseSettings):
    """单条性能告警规则（方案 24 §3.2）。"""

    enabled: bool = True
    above: float | None = None  # 越大越坏判据（与 below 二选一，方向即判据）
    below: float | None = None  # 越小越坏判据
    consecutive: int = 3        # 连续越限 N 次触发（按有效判定样本计）
    clear_margin: float = 5.0   # 迟滞余量：解除需回落/回升越过该余量
    clear_consecutive: int = 3  # 连续安全 M 次解除
    cooldown: float = 60.0      # 通知冷却（秒）；冷却内触发进徽标但不弹 toast


class MetricAlertsConfig(BaseSettings):
    """各指标的告警规则集（metrics.thresholds，方案 24）。"""

    cpu_percent: AlertRuleConfig = Field(default_factory=lambda: AlertRuleConfig(above=80.0))
    memory_percent: AlertRuleConfig = Field(default_factory=lambda: AlertRuleConfig(above=90.0))
    fps: AlertRuleConfig = Field(default_factory=lambda: AlertRuleConfig(below=30.0))
    rx_rate_kbps: AlertRuleConfig = Field(
        default_factory=lambda: AlertRuleConfig(enabled=False, above=0.0)
    )
    tx_rate_kbps: AlertRuleConfig = Field(
        default_factory=lambda: AlertRuleConfig(enabled=False, above=0.0)
    )
```

然后 `MetricsConfig` 末尾加一行：

```python
    thresholds: MetricAlertsConfig = Field(default_factory=MetricAlertsConfig)
```

- [ ] **Step 2: base.yaml 增加 thresholds 节**

在 `config/base.yaml` metrics 节的 `max_rows_total: 500000` 行之后追加（缩进与既有键一致，两空格）：

```yaml
  # 阈值告警（方案 24）：连续 N 次越限触发；迟滞余量 + 连续 M 次安全解除；
  # 冷却期内重复触发仍进徽标但不弹 toast。consecutive 按「有效判定样本数」计
  # （fps 仅计实测 gfxinfo 窗口），各指标墙钟时限见 方案/24 §3.1 时限表
  thresholds:
    cpu_percent:    { above: 80.0, consecutive: 3, clear_margin: 5.0, clear_consecutive: 3, cooldown: 60 }
    memory_percent: { above: 90.0, consecutive: 3, clear_margin: 5.0, clear_consecutive: 3, cooldown: 60 }
    fps:            { below: 30.0, consecutive: 3, clear_margin: 5.0, clear_consecutive: 3, cooldown: 60 }
    rx_rate_kbps:   { enabled: false, above: 0.0, consecutive: 3, clear_margin: 0.0, clear_consecutive: 3, cooldown: 60 }
    tx_rate_kbps:   { enabled: false, above: 0.0, consecutive: 3, clear_margin: 0.0, clear_consecutive: 3, cooldown: 60 }
```

- [ ] **Step 3: 更新 tests/unit/test_metrics_config.py 两处精确断言**

`test_metrics_config_defaults` 末尾追加：

```python
    th = metrics.thresholds
    assert th.cpu_percent.enabled is True
    assert th.cpu_percent.above == 80.0 and th.cpu_percent.below is None
    assert th.memory_percent.above == 90.0
    assert th.fps.below == 30.0 and th.fps.above is None
    assert th.rx_rate_kbps.enabled is False
    assert th.tx_rate_kbps.enabled is False
    assert th.cpu_percent.consecutive == 3
    assert th.cpu_percent.clear_margin == 5.0
    assert th.cpu_percent.clear_consecutive == 3
    assert th.cpu_percent.cooldown == 60.0
```

`test_base_yaml_has_metrics_section` 的期望字典改为（新增 `thresholds` 键，其余键原样保留）：

```python
    assert section == {
        "buffer_size": 3600,
        "network_buffer_size": 1800,
        "network_interval": 2.0,
        "idle_ttl_seconds": 300,
        "lost_failures": 3,
        "recording": False,
        "retention_days": 3,
        "max_rows_total": 500000,
        "thresholds": {
            "cpu_percent": {"above": 80.0, "consecutive": 3, "clear_margin": 5.0,
                            "clear_consecutive": 3, "cooldown": 60},
            "memory_percent": {"above": 90.0, "consecutive": 3, "clear_margin": 5.0,
                               "clear_consecutive": 3, "cooldown": 60},
            "fps": {"below": 30.0, "consecutive": 3, "clear_margin": 5.0,
                    "clear_consecutive": 3, "cooldown": 60},
            "rx_rate_kbps": {"enabled": False, "above": 0.0, "consecutive": 3,
                             "clear_margin": 0.0, "clear_consecutive": 3, "cooldown": 60},
            "tx_rate_kbps": {"enabled": False, "above": 0.0, "consecutive": 3,
                             "clear_margin": 0.0, "clear_consecutive": 3, "cooldown": 60},
        },
    }
```

同文件新增覆盖解析用例（yaml 部分覆盖 → 嵌套模型，方案 §3.2 加载路径）：

```python
def test_thresholds_partial_override_parses() -> None:
    """YAML 局部覆盖经 Settings(**config) 解析为嵌套模型，未覆盖字段回落默认。"""
    from config.settings import Settings

    s = Settings(metrics={"thresholds": {"cpu_percent": {"above": 50.0}}})

    assert s.metrics.thresholds.cpu_percent.above == 50.0
    assert s.metrics.thresholds.cpu_percent.consecutive == 3   # 未覆盖字段回落默认
    assert s.metrics.thresholds.fps.below == 30.0              # 未覆盖指标回落默认
```

- [ ] **Step 4: 运行配置测试确认通过**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_metrics_config.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 写失败测试 tests/application/test_alert_engine.py**

完整文件内容：

```python
"""告警评估引擎测试（方案 24 §6-1~6，T1 交付）。

覆盖：连续判定 / 迟滞解除 / 冷却抑制 / notify 窗口 / 无效样本两条路径 /
无效规则跳过 / sync 热重载语义 / 快照形状。
"""

import time

import pytest

from app.application.alert_engine import AlertEngine, AlertRule, build_rules

WINDOW = 5.0


def make_rule(**overrides: object) -> AlertRule:
    defaults: dict[str, object] = {
        "id": "cpu_percent",
        "above": 80.0,
        "below": None,
        "consecutive": 3,
        "clear_margin": 5.0,
        "clear_consecutive": 3,
        "cooldown": 60.0,
    }
    defaults.update(overrides)
    return AlertRule(**defaults)  # type: ignore[arg-type]


def make_engine(rules: list[AlertRule] | None = None) -> AlertEngine:
    engine = AlertEngine(notify_window=WINDOW)
    engine.sync(rules if rules is not None else [make_rule()])
    return engine


def test_consecutive_breach_triggers_on_nth() -> None:
    """N=3：前 2 次越限不触发，第 3 次触发（since=触发样本 ts）。"""
    engine = make_engine()
    t0 = time.time()

    engine.evaluate("cpu_percent", 85.0, t0)
    engine.evaluate("cpu_percent", 85.0, t0 + 1)
    assert engine.snapshot(t0 + 1) == []

    engine.evaluate("cpu_percent", 85.0, t0 + 2)
    alerts = engine.snapshot(t0 + 2)
    assert len(alerts) == 1
    assert alerts[0] == {
        "id": "cpu_percent",
        "value": 85.0,
        "threshold": 80.0,
        "direction": "above",
        "since": t0 + 2,
        "notify": True,
    }


def test_notify_only_within_window() -> None:
    """notify 按窗口匹配：窗口内为 True（多帧），窗外为 False。"""
    engine = make_engine()
    t0 = time.time()
    for i in range(3):
        engine.evaluate("cpu_percent", 85.0, t0 + i)

    assert engine.snapshot(t0 + 2)[0]["notify"] is True                  # 触发帧（差 0）
    assert engine.snapshot(t0 + 2 + WINDOW - 0.1)[0]["notify"] is True   # 窗口内（差 4.9 ≤ 5.0）
    assert engine.snapshot(t0 + 2 + WINDOW + 0.1)[0]["notify"] is False  # 窗口外（差 5.1 > 5.0）


def test_breach_counter_reset_by_non_breach_sample() -> None:
    """非活动时非越限样本（78 低于 above=80）不计入 breach → 计数清零。"""
    engine = make_engine()
    t0 = time.time()
    engine.evaluate("cpu_percent", 85.0, t0)
    engine.evaluate("cpu_percent", 85.0, t0 + 1)
    engine.evaluate("cpu_percent", 78.0, t0 + 2)   # 非越限 → 计数清零
    engine.evaluate("cpu_percent", 85.0, t0 + 3)
    engine.evaluate("cpu_percent", 85.0, t0 + 4)
    assert engine.snapshot(t0 + 4) == []           # 只累计到 2 次
    engine.evaluate("cpu_percent", 85.0, t0 + 5)
    assert len(engine.snapshot(t0 + 5)) == 1


def test_hysteresis_requires_margin_and_consecutive_to_clear() -> None:
    """解除需越过迟滞余量（above: <75）且连续 M=3 次；迟滞带内保持活动。"""
    engine = make_engine()
    t0 = time.time()
    for i in range(3):
        engine.evaluate("cpu_percent", 85.0, t0 + i)
    assert len(engine.snapshot(t0 + 2)) == 1

    # 迟滞带内（78 不小于 75）：保持活动，安全计数被清零
    engine.evaluate("cpu_percent", 78.0, t0 + 3)
    engine.evaluate("cpu_percent", 78.0, t0 + 4)
    assert len(engine.snapshot(t0 + 4)) == 1

    # 越过余量但不足 M 次：仍活动
    engine.evaluate("cpu_percent", 74.0, t0 + 5)
    engine.evaluate("cpu_percent", 74.0, t0 + 6)
    assert len(engine.snapshot(t0 + 6)) == 1

    engine.evaluate("cpu_percent", 74.0, t0 + 7)
    assert engine.snapshot(t0 + 7) == []


def test_below_direction_uses_plus_margin() -> None:
    """below 规则（fps<30）：触发解除需回升越过 threshold+margin（>35）。"""
    engine = make_engine([make_rule(id="fps", above=None, below=30.0)])
    t0 = time.time()
    for i in range(3):
        engine.evaluate("fps", 25.0, t0 + i)
    assert len(engine.snapshot(t0 + 2)) == 1

    engine.evaluate("fps", 34.0, t0 + 3)   # 迟滞带内，保持
    assert len(engine.snapshot(t0 + 3)) == 1
    for i in range(3):
        engine.evaluate("fps", 40.0, t0 + 4 + i)
    assert engine.snapshot(t0 + 6) == []


def test_cooldown_suppresses_notify_but_keeps_badge() -> None:
    """冷却内新 episode：活动恢复但 notify=False；超冷却后新 episode notify=True。"""
    engine = make_engine()
    t0 = time.time()
    for i in range(3):
        engine.evaluate("cpu_percent", 85.0, t0 + i)
    assert engine.snapshot(t0 + 2)[0]["notify"] is True

    # 解除（3 次安全）
    for i in range(3):
        engine.evaluate("cpu_percent", 70.0, t0 + 3 + i)
    assert engine.snapshot(t0 + 5) == []

    # 冷却内再次触发（距上次通知 30s < cooldown 60）
    t1 = t0 + 33
    for i in range(3):
        engine.evaluate("cpu_percent", 85.0, t1 + i)
    alerts = engine.snapshot(t1 + 2)
    assert len(alerts) == 1                    # 照常进徽标
    assert alerts[0]["notify"] is False        # 冷却抑制 toast
    assert alerts[0]["since"] == t1 + 2        # 新 episode

    # 解除后等超冷却再次触发（距上次通知 > 60s）
    for i in range(3):
        engine.evaluate("cpu_percent", 70.0, t1 + 3 + i)
    t2 = t0 + 100
    for i in range(3):
        engine.evaluate("cpu_percent", 85.0, t2 + i)
    assert engine.snapshot(t2 + 2)[0]["notify"] is True


def test_invalid_samples_are_skipped_entirely() -> None:
    """None 与 fresh=False 样本不触碰任何计数器（不触发、不解除、不重置）。"""
    engine = make_engine()
    t0 = time.time()

    # 无效样本不会触发
    for i in range(10):
        engine.evaluate("cpu_percent", None, t0 + i)
        engine.evaluate("cpu_percent", 85.0, t0 + i, fresh=False)
    assert engine.snapshot(t0 + 9) == []

    # 有效样本穿插无效样本：累积不被重置、无效不计数
    engine.evaluate("cpu_percent", 85.0, t0 + 10)
    engine.evaluate("cpu_percent", None, t0 + 11)
    engine.evaluate("cpu_percent", 85.0, t0 + 12, fresh=False)
    engine.evaluate("cpu_percent", 85.0, t0 + 13)
    engine.evaluate("cpu_percent", 85.0, t0 + 14)
    assert len(engine.snapshot(t0 + 14)) == 1  # 第 3 个有效越限样本触发

    # 活动态下无效样本不解除
    for i in range(10):
        engine.evaluate("cpu_percent", None, t0 + 15 + i)
        engine.evaluate("cpu_percent", 10.0, t0 + 15 + i, fresh=False)
    assert len(engine.snapshot(t0 + 24)) == 1


def test_sync_removal_reset_and_param_change() -> None:
    """sync：移除丢状态；重新加回从零起；参数变化计数重置。"""
    engine = make_engine()
    t0 = time.time()
    for i in range(3):
        engine.evaluate("cpu_percent", 85.0, t0 + i)
    assert len(engine.snapshot(t0 + 2)) == 1

    engine.sync([])                                  # 移除（等效禁用）
    assert engine.snapshot(t0 + 2) == []
    engine.evaluate("cpu_percent", 85.0, t0 + 3)     # 无规则 → no-op
    assert engine.snapshot(t0 + 3) == []

    engine.sync([make_rule()])                       # 重新加回 → 从零起算
    engine.evaluate("cpu_percent", 85.0, t0 + 4)
    engine.evaluate("cpu_percent", 85.0, t0 + 5)
    assert engine.snapshot(t0 + 5) == []             # 只累计 2 次

    # 参数变化 → 计数重置（此处换 id 相同但 consecutive 变化的规则）
    engine.sync([make_rule(consecutive=2)])
    engine.evaluate("cpu_percent", 85.0, t0 + 6)
    assert engine.snapshot(t0 + 6) == []             # 新计数从 1 开始
    engine.evaluate("cpu_percent", 85.0, t0 + 7)
    assert len(engine.snapshot(t0 + 7)) == 1


def test_build_rules_skips_invalid_and_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_rules：disabled 过滤；above/below 同缺同存 → 跳过且不抛。"""
    from types import SimpleNamespace

    from config.settings import settings as real_settings

    metrics = real_settings().metrics.model_copy(deep=True)
    metrics.thresholds.rx_rate_kbps.enabled = True
    metrics.thresholds.rx_rate_kbps.above = None      # 同缺 → 无效
    metrics.thresholds.tx_rate_kbps.enabled = True
    metrics.thresholds.tx_rate_kbps.above = None
    metrics.thresholds.tx_rate_kbps.below = 10.0      # 同存 → 无效
    monkeypatch.setattr(
        "app.application.alert_engine.settings",
        lambda: SimpleNamespace(metrics=metrics),
    )

    rules = build_rules()

    ids = [r.id for r in rules]
    assert ids == ["cpu_percent", "memory_percent", "fps"]   # rx/tx 已启用但参数无效 → 均跳过
    cpu = next(r for r in rules if r.id == "cpu_percent")
    assert cpu.above == 80.0 and cpu.cooldown == 60.0


def test_build_rules_disabled_metric_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """enabled=False 的指标不产出规则（热重载禁用 → sync 视为移除）。"""
    from types import SimpleNamespace

    from config.settings import settings as real_settings

    metrics = real_settings().metrics.model_copy(deep=True)
    metrics.thresholds.cpu_percent.enabled = False
    monkeypatch.setattr(
        "app.application.alert_engine.settings",
        lambda: SimpleNamespace(metrics=metrics),
    )

    ids = [r.id for r in build_rules()]
    assert "cpu_percent" not in ids
```

- [ ] **Step 6: 运行测试确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/application/test_alert_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.application.alert_engine'`。

- [ ] **Step 7: 实现 backend/app/application/alert_engine.py**

完整文件内容：

```python
"""告警评估引擎（纯逻辑状态机）
================================

层：应用层——无 I/O、无框架依赖，同 bitrate_advisor.py 先例（方案 24）。

按设备实例化；每规则独立状态机。状态转移：

    非活动 --连续 N 次越限--> 活动（fired_ts=ts；冷却允许时记 notified_ts）
    活动   --连续 M 次「越过迟滞余量」的安全样本--> 非活动
    迟滞带内样本：保持现状、计数清零
    无效样本（value=None 或 fresh=False）：整条跳过，不触碰任何计数器
"""

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from config.settings import settings

logger = get_logger(__name__)

# 告警指标全集（与 base.yaml thresholds 节键一致）
METRIC_IDS: tuple[str, ...] = (
    "cpu_percent",
    "memory_percent",
    "fps",
    "rx_rate_kbps",
    "tx_rate_kbps",
)


@dataclass(frozen=True)
class AlertRule:
    """单条有效规则（id=指标名；above/below 二选一，方向即判据）。"""

    id: str
    above: float | None
    below: float | None
    consecutive: int
    clear_margin: float
    clear_consecutive: int
    cooldown: float


@dataclass
class _RuleState:
    breach_count: int = 0
    ok_count: int = 0
    active: bool = False
    fired_ts: float | None = None
    notified_ts: float | None = None
    last_value: float | None = None


class AlertEngine:
    """按设备实例化的告警状态机集合。

    构造参数：
        notify_window: 通知窗口（秒）——触发后窗口内的快照 notify=True，
            由服务按采样间隔推导（max(3×interval, 5.0)）。客户端按
            id:since（episode）去重后只弹一次。
    """

    def __init__(self, notify_window: float) -> None:
        self.notify_window = notify_window
        self._rules: dict[str, AlertRule] = {}
        self._states: dict[str, _RuleState] = {}
        # 冷却基准跨 episode 持久（上次通知时刻），规则移除时一并丢弃
        self._last_notify_ts: dict[str, float] = {}

    def sync(self, rules: list[AlertRule]) -> None:
        """按 id 差异同步规则集（热重载）：移除丢状态；新增从零起；参数变化重置计数。"""
        incoming = {r.id: r for r in rules}
        for rid in [rid for rid in self._rules if rid not in incoming]:
            del self._rules[rid]
            del self._states[rid]
            self._last_notify_ts.pop(rid, None)
        for rid, rule in incoming.items():
            current = self._rules.get(rid)
            if current is None:
                self._rules[rid] = rule
                self._states[rid] = _RuleState()
            elif current != rule:
                # 参数变化：计数与活动态重置；冷却基准保留（防重载后立刻重弹）
                self._rules[rid] = rule
                self._states[rid] = _RuleState()

    def evaluate(
        self, metric_id: str, value: float | None, ts: float, fresh: bool = True
    ) -> None:
        """评估一个样本；无效样本（None 或 fresh=False）整条跳过。"""
        rule = self._rules.get(metric_id)
        if rule is None:
            return
        if value is None or not fresh:
            return

        state = self._states[metric_id]
        state.last_value = value

        if state.active:
            if self._is_safe(rule, value):
                state.ok_count += 1
                if state.ok_count >= rule.clear_consecutive:
                    self._deactivate(state)
            else:
                # 迟滞带内或仍越限：保持活动，安全计数清零
                state.ok_count = 0
            return

        if self._is_breach(rule, value):
            state.breach_count += 1
            if state.breach_count >= rule.consecutive:
                self._activate(metric_id, rule, state, ts)
        else:
            state.breach_count = 0

    def snapshot(self, sample_ts: float) -> list[dict[str, Any]]:
        """当前活动告警快照；notify 按通知窗口标记（客户端负责 episode 去重）。"""
        alerts: list[dict[str, Any]] = []
        for rid, rule in self._rules.items():
            state = self._states.get(rid)
            if state is None or not state.active:
                continue
            notify = (
                state.notified_ts is not None
                and 0.0 <= sample_ts - state.notified_ts <= self.notify_window
            )
            alerts.append(
                {
                    "id": rid,
                    "value": state.last_value,
                    "threshold": rule.above if rule.above is not None else rule.below,
                    "direction": "above" if rule.above is not None else "below",
                    "since": state.fired_ts,
                    "notify": notify,
                }
            )
        return alerts

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _activate(
        self, metric_id: str, rule: AlertRule, state: _RuleState, ts: float
    ) -> None:
        state.active = True
        state.fired_ts = ts
        state.breach_count = 0
        state.ok_count = 0
        last_notify = self._last_notify_ts.get(metric_id)
        if last_notify is None or ts - last_notify >= rule.cooldown:
            state.notified_ts = ts
            self._last_notify_ts[metric_id] = ts
        else:
            state.notified_ts = None  # 冷却抑制：进徽标不弹 toast

    @staticmethod
    def _deactivate(state: _RuleState) -> None:
        state.active = False
        state.fired_ts = None
        state.notified_ts = None
        state.breach_count = 0
        state.ok_count = 0

    @staticmethod
    def _is_breach(rule: AlertRule, value: float) -> bool:
        if rule.above is not None:
            return value > rule.above
        if rule.below is not None:
            return value < rule.below
        return False

    @staticmethod
    def _is_safe(rule: AlertRule, value: float) -> bool:
        if rule.above is not None:
            return value < rule.above - rule.clear_margin
        if rule.below is not None:
            return value > rule.below + rule.clear_margin
        return False


def build_rules(metric_ids: tuple[str, ...] = METRIC_IDS) -> list[AlertRule]:
    """从配置构建规则集（只产出 enabled 且方向合法的规则，方案 24 §4.1）。"""
    thresholds = settings().metrics.thresholds
    rules: list[AlertRule] = []
    for mid in metric_ids:
        cfg = getattr(thresholds, mid)
        if not cfg.enabled:
            continue
        if (cfg.above is None) == (cfg.below is None):
            logger.warning(
                "alert_rule_invalid", metric=mid, above=cfg.above, below=cfg.below
            )
            continue
        rules.append(
            AlertRule(
                id=mid,
                above=cfg.above,
                below=cfg.below,
                consecutive=cfg.consecutive,
                clear_margin=cfg.clear_margin,
                clear_consecutive=cfg.clear_consecutive,
                cooldown=cfg.cooldown,
            )
        )
    return rules
```

- [ ] **Step 8: 运行测试确认通过**

Run: `PYTHONPATH=backend:. python -m pytest tests/application/test_alert_engine.py tests/unit/test_metrics_config.py -q`
Expected: 全部 PASS。

- [ ] **Step 9: 门禁 + 提交**

```bash
python -m ruff check backend/app && python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q
git add config/settings.py config/base.yaml backend/app/application/alert_engine.py tests/application/test_alert_engine.py tests/unit/test_metrics_config.py
git commit -m "$(cat <<'EOF'
feat: 方案 24 T1 阈值配置与告警评估器（AlertEngine 状态机）

- config/settings.py：AlertRuleConfig/MetricAlertsConfig 嵌套模型 + metrics.thresholds
- config/base.yaml：thresholds 节（cpu 80 / mem 90 / fps 30；网络速率默认禁用）
- backend/app/application/alert_engine.py：连续判定 + 迟滞解除 + 冷却抑制 +
  notify 窗口匹配 + fresh/None 无效样本跳过 + sync 热重载语义
- 单测覆盖方案 §6-1~6；test_metrics_config 精确断言同步更新

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 采样器 fps_fresh + 两服务挂点 + 接口内嵌

**Files:**
- Modify: `backend/app/infrastructure/performance/sampler.py`（`PerformanceMetrics.fps_fresh` + `_get_fps_and_jank` 返回三元组）
- Modify: `backend/app/application/performance_service.py`（引擎挂点 + `get_alerts`）
- Modify: `backend/app/application/network_service.py`（引擎挂点 + `get_alerts` + 两处释放）
- Modify: `backend/app/interfaces/ws/performance.py`（消息加 `alerts` 键）
- Modify: `backend/app/interfaces/http/network.py`（stats 响应加 `alerts` 键）
- Modify: `tests/infrastructure/test_sampler.py`（fps_fresh 用例）
- Modify: `tests/application/test_performance_service.py`（引擎挂点/释放/快照集成用例）
- Modify: `tests/application/test_network_service.py`（引擎挂点/释放/评估用例，沿用既有 wait_until/fast_loop）
- Modify: `tests/unit/test_http_network.py`（替身补 `get_alerts` + 期望更新 + 新用例）
- Create: `tests/unit/test_ws_performance.py`

**Interfaces:**
- Consumes: Task 1 的 `AlertEngine/AlertRule/build_rules`、`settings().metrics.thresholds`。
- Produces: WS perf 消息与 `/api/network/{id}/stats` 响应携带 `"alerts": [...]`（形状见方案 §4.2）；`PerformanceService.get_alerts(device_id, sample_ts)`、`NetworkService.get_alerts(device_id, sample_ts)`（同步方法，无引擎返回 `[]`）。

- [ ] **Step 1: 写失败测试（采样器 fps_fresh）**

在 `tests/infrastructure/test_sampler.py` 末尾追加（沿用既有 `FakeAdb`/`GOOD_OUTPUTS`；文件头部已 `import asyncio`，需补 `import time`）：

```python
async def test_fps_fresh_marks_measured_round_only():
    """fps_fresh：实测轮 True；首轮空窗与应用切换沿用均 False（方案 24 §4.3）。"""
    adb = FakeAdb(GOOD_OUTPUTS)
    sampler = PerformanceSampler(adb, "device-X")
    now = time.time()

    fps, jank, fresh = await sampler._get_fps_and_jank(now, "com.example.app")
    assert (fps, jank, fresh) == (None, 0, False)     # 首轮无基准

    sampler._prev_frames = 100                        # 与 gfxinfo 输出一致（100 帧）
    sampler._prev_jank = 0
    sampler._prev_ts = now - 1.0
    sampler._prev_package = "com.example.app"
    fps, jank, fresh = await sampler._get_fps_and_jank(now + 1.0, "com.example.app")
    assert fresh is True                              # 实测分支
    assert fps is not None

    fps, jank, fresh = await sampler._get_fps_and_jank(now + 2.0, "other.app")
    assert fresh is False                             # 应用切换沿用
    assert fps == sampler._last_fps


async def test_collect_once_fps_fresh_false_on_reuse_round():
    """非 gfxinfo 轮沿用 _last_fps：fps_fresh 必须为 False。"""
    adb = FakeAdb(GOOD_OUTPUTS)
    sampler = PerformanceSampler(adb, "device-X")
    await sampler._collect_once()                     # 轮 1
    sampler._last_fps = 25.5

    m = await sampler._collect_once()                 # 轮 2：非 gfx 轮

    assert m.fps == 25.5
    assert m.fps_fresh is False
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/infrastructure/test_sampler.py -q`
Expected: FAIL — `_get_fps_and_jank` 返回 2 元组（解包 ValueError）/ `PerformanceMetrics` 无 `fps_fresh`。

- [ ] **Step 3: 修改 sampler.py**

三处改动：

1. `PerformanceMetrics` 末尾加字段（带默认值，既有构造点向后兼容）：

```python
    top_package: str          # 当前前台应用包名
    # fps 是否为本轮实测值（沿用 _last_fps / 空窗为 False）。仅服务端告警判定用，
    # 不进 _metrics_to_dict / WS 消息 / 落库（方案 24 §4.3）
    fps_fresh: bool = False
```

2. `_collect_once`（约 :132-146）：改为三元组解包并透传：

```python
        if self._round % self.GFXINFO_INTERVAL == 1:
            fps, jank, fps_fresh = await self._get_fps_and_jank(now, package)
        else:
            fps, jank, fps_fresh = self._last_fps, 0, False

        return PerformanceMetrics(
            ts=now,
            cpu_percent=cpu,
            total_memory_mb=mem_total,
            used_memory_mb=mem_used,
            fps=fps,
            jank_count=jank,
            current_activity=activity,
            top_package=package,
            fps_fresh=fps_fresh,
        )
```

3. `_get_fps_and_jank`：签名改 `-> tuple[float | None, int, bool]`，文档字符串「(fps, jank_delta)` 元组」改为「(fps, jank_delta, fresh) 元组；fresh 表示 fps 是否为实测值」，并改全部 return 点：

- `if not package: return None, 0` → `return None, 0, False`
- `if not frames_match: return None, 0` → `return None, 0, False`
- fps 计算块：

```python
            fps: float | None = None
            fresh = False
            jank_delta = 0
            if self._prev_frames is not None and self._prev_ts is not None:
                elapsed = now - self._prev_ts
                if elapsed > 0:
                    frame_delta = total_frames - self._prev_frames
                    # 检测应用切换（包名变化）或计数器重置
                    if self._prev_package != package:
                        # 应用切换，重置基准，返回上次有效 FPS 作为过渡
                        fps = self._last_fps
                    elif frame_delta < 0 or frame_delta > 10000:
                        # 计数器异常，重置基准
                        fps = self._last_fps
                    else:
                        fps = round(frame_delta / elapsed, 1)
                        self._last_fps = fps  # 记录有效 FPS
                        fresh = True
                        jank_delta = max(0, total_jank - self._prev_jank)
```

- 末尾 `return fps, jank_delta` → `return fps, jank_delta, fresh`
- `except Exception` 分支 `return None, 0` → `return None, 0, False`

- [ ] **Step 4: 运行采样器测试确认通过**

Run: `PYTHONPATH=backend:. python -m pytest tests/infrastructure/test_sampler.py -q`
Expected: PASS（既有用例不受影响——它们只经 `_collect_once`/`sample()` 断言）。

- [ ] **Step 5: 写服务层失败测试**

`tests/application/test_performance_service.py`：顶部补 `import time`（现有 import 区为 `import asyncio` + `from collections import deque`），文件末尾追加（等待范式沿用该文件既有 `for _ in range(500): ... await asyncio.sleep(0.01)` 轮询）：

```python
class HighCpuAdb:
    """CPU 恒 100%（user 递增、idle 不动）、内存 50% 的假 ADB。"""

    def __init__(self) -> None:
        self._user = 100
        self.calls: list[str] = []

    async def shell(self, device_id: str, cmd: str) -> str:
        self.calls.append(cmd)
        if cmd.startswith("cat /proc/stat"):
            self._user += 100
            return f"cpu  {self._user} 0 0 100 0 0 0 0 0 0\n"
        if cmd.startswith("cat /proc/meminfo"):
            return "MemTotal: 2048000 kB\nMemAvailable: 1024000 kB\n"
        if cmd.startswith("dumpsys activity"):
            return "mResumedActivity: ActivityRecord{abc123 u0 com.example.app/.MainActivity t123}\n"
        if cmd.startswith("dumpsys gfxinfo"):
            return "Total frames rendered: 100\nJanky frames: 5\n"
        return ""


async def test_get_alerts_snapshot_after_consecutive_breach():
    """连续 CPU 越限（默认 above=80）→ get_alerts 返回活动快照；stop 后清空（方案 24 §7）。"""
    service = PerformanceService(HighCpuAdb())
    await service.start_monitoring("dev-A", interval=0)

    alerts: list[dict] = []
    for _ in range(500):
        alerts = service.get_alerts("dev-A", time.time())
        if alerts:
            break
        await asyncio.sleep(0.01)

    assert alerts, "连续越限后应有活动告警"
    assert alerts[0]["id"] == "cpu_percent"
    assert alerts[0]["direction"] == "above"
    assert alerts[0]["threshold"] == 80.0
    assert alerts[0]["notify"] is True          # 首次触发，窗口内
    assert isinstance(alerts[0]["since"], float)

    await service.stop_monitoring("dev-A")
    assert service.get_alerts("dev-A", time.time()) == []   # D5：释放即清空


async def test_alert_engine_released_when_device_lost():
    """失联自然结束路径（_release_device 单点）同样释放引擎。"""
    service = PerformanceService(DeadAdb())
    await service.start_monitoring("dev-B", interval=0)

    for _ in range(500):
        if "dev-B" not in service._tasks:
            break
        await asyncio.sleep(0.01)

    assert "dev-B" not in service._alert_engines
    assert service.get_alerts("dev-B", time.time()) == []


async def test_sampling_loop_evaluates_each_round():
    """采样循环每轮 sync + evaluate 各指标（先评估后推送）。"""
    service = PerformanceService(HighCpuAdb())
    await service.start_monitoring("dev-C", interval=0)

    class SpyEngine:
        def __init__(self) -> None:
            self.synced = 0
            self.evaluated: list[str] = []

        def sync(self, rules: list) -> None:
            self.synced += 1

        def evaluate(self, metric_id: str, value: object, ts: float, fresh: bool = True) -> None:
            self.evaluated.append(metric_id)

        def snapshot(self, sample_ts: float) -> list:
            return []

    spy = SpyEngine()
    service._alert_engines["dev-C"] = spy  # type: ignore[assignment]

    for _ in range(500):
        if spy.synced >= 2:
            break
        await asyncio.sleep(0.01)

    await service.stop_monitoring("dev-C")
    assert spy.synced >= 2
    assert {"cpu_percent", "memory_percent", "fps"} <= set(spy.evaluated)
```

`tests/application/test_network_service.py` 追加（沿用文件内既有 `wait_until` / `fast_loop` fixture 与 `DEV`/`std_outputs`/`FakeAdb`/`DeadAdb`，勿自拟轮询循环）：

```python
async def test_get_alerts_empty_without_engine():
    """无监控（无引擎）时 get_alerts 返回 []。"""
    service = NetworkService(FakeAdb(std_outputs()))
    assert service.get_alerts(DEV, time.time()) == []


async def test_alert_engine_created_and_released_on_stop():
    """start 创建引擎；stop_monitoring 释放（网络服务无 _release_device 单点，走其一处）。"""
    service = NetworkService(FakeAdb(std_outputs()))
    await service.start_monitoring(DEV)

    assert DEV in service._alert_engines
    await service.stop_monitoring(DEV)
    assert DEV not in service._alert_engines


async def test_alert_engine_released_on_device_lost(monkeypatch, fast_loop) -> None:
    """失联自然结束路径（_sampling_loop finally）同样释放引擎。"""
    monkeypatch.setattr(settings().metrics, "lost_failures", 3)
    service = NetworkService(DeadAdb())
    await service.start_monitoring(DEV)

    await wait_until(lambda: DEV not in service._tasks)

    assert DEV not in service._alert_engines
    assert service.get_alerts(DEV, time.time()) == []


async def test_sampling_loop_evaluates_rate_metrics(monkeypatch, fast_loop) -> None:
    """采样循环每轮 sync + evaluate rx/tx 两指标。"""
    service = NetworkService(FakeAdb(std_outputs()))
    await service.start_monitoring(DEV)

    class SpyEngine:
        def __init__(self) -> None:
            self.synced = 0
            self.evaluated: list[str] = []

        def sync(self, rules: list) -> None:
            self.synced += 1

        def evaluate(self, metric_id: str, value: object, ts: float, fresh: bool = True) -> None:
            self.evaluated.append(metric_id)

        def snapshot(self, sample_ts: float) -> list:
            return []

    spy = SpyEngine()
    service._alert_engines[DEV] = spy  # type: ignore[assignment]

    await wait_until(lambda: spy.synced >= 1 and "rx_rate_kbps" in spy.evaluated)

    await service.stop_monitoring(DEV)
    assert "tx_rate_kbps" in spy.evaluated
```

- [ ] **Step 6: 运行确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/application/test_performance_service.py tests/application/test_network_service.py -q`
Expected: FAIL — `AttributeError: 'PerformanceService' object has no attribute 'get_alerts'` / `'_alert_engines'`。

- [ ] **Step 7: 实现两服务挂点**

`backend/app/application/performance_service.py`：

1. import 区加：`from app.application.alert_engine import AlertEngine, build_rules`。
2. `__init__` 末尾加：`self._alert_engines: dict[str, AlertEngine] = {}`。
3. `start_monitoring` 在 `self._subscribers[device_id] = []` 之后加：

```python
        self._alert_engines[device_id] = AlertEngine(notify_window=max(3 * interval, 5.0))
```

4. `_sampling_loop` 内 `self._buffers[device_id].append(metrics)` 之后、推订阅者之前插入：

```python
                # 告警评估（方案 24）：先评估后推送，订阅者唤醒时快照已就绪
                engine = self._alert_engines.get(device_id)
                if engine is not None:
                    engine.sync(build_rules())
                    engine.evaluate("cpu_percent", metrics.cpu_percent, metrics.ts)
                    engine.evaluate(
                        "memory_percent", self._memory_percent(metrics), metrics.ts
                    )
                    engine.evaluate(
                        "fps", metrics.fps, metrics.ts, fresh=metrics.fps_fresh
                    )
```

5. `_release_device` 内 `self._buffers.pop(device_id, None)` 之后加：

```python
        self._alert_engines.pop(device_id, None)
```

6. `get_metrics` 之前（或 `_release_device` 之后）加：

```python
    @staticmethod
    def _memory_percent(m: PerformanceMetrics) -> float | None:
        """内存使用率（百分比）；total=0 时返回 None（引擎跳过该样本）。"""
        if m.total_memory_mb <= 0:
            return None
        return m.used_memory_mb / m.total_memory_mb * 100

    def get_alerts(self, device_id: str, sample_ts: float) -> list[dict[str, Any]]:
        """当前活动告警快照（纯内存读取；无引擎时为空，方案 24 D5）。"""
        engine = self._alert_engines.get(device_id)
        if engine is None:
            return []
        return engine.snapshot(sample_ts)
```

`backend/app/application/network_service.py`：

1. import 区加：`from app.application.alert_engine import AlertEngine, build_rules`。
2. `__init__` 末尾加：`self._alert_engines: dict[str, AlertEngine] = {}`。
3. `start_monitoring` 在 `self._schedule_idle_guard(device_id)` 之前加：

```python
        self._alert_engines[device_id] = AlertEngine(
            notify_window=max(3 * float(settings().metrics.network_interval), 5.0)
        )
```

4. `_sampling_loop` 内 `self._buffers[device_id].append(stats)` 之后插入：

```python
                # 告警评估（方案 24）：每轮实测，fresh 恒 True
                engine = self._alert_engines.get(device_id)
                if engine is not None:
                    engine.sync(build_rules())
                    engine.evaluate("rx_rate_kbps", stats.rx_rate_kbps, stats.ts)
                    engine.evaluate("tx_rate_kbps", stats.tx_rate_kbps, stats.ts)
```

5. `_sampling_loop` 的 `finally` 内（三处 pop 之后）加：`self._alert_engines.pop(device_id, None)`。
6. `stop_monitoring` 内（`self._buffers.pop(device_id, None)` 之后）加：`self._alert_engines.pop(device_id, None)`。
7. 加 `get_alerts`（同 performance 形态）：

```python
    def get_alerts(self, device_id: str, sample_ts: float) -> list[dict[str, Any]]:
        """当前活动告警快照（纯内存读取；无引擎时为空，方案 24 D5）。"""
        engine = self._alert_engines.get(device_id)
        if engine is None:
            return []
        return engine.snapshot(sample_ts)
```

（`Any` 已在该文件 import 中——若缺则补 `from typing import Any`。）

- [ ] **Step 8: 运行服务层测试确认通过**

Run: `PYTHONPATH=backend:. python -m pytest tests/application/test_performance_service.py tests/application/test_network_service.py tests/infrastructure/test_sampler.py -q`
Expected: 全部 PASS。

- [ ] **Step 9: 写接口层失败测试**

新建 `tests/unit/test_ws_performance.py`：

```python
"""WS perf 端点（interfaces/ws/performance.py）消息形状测试（方案 24 §6-10）。

样式沿用 test_ws_debug.py：手写 Fake（不依赖真机 / Starlette 运行时），直调 handler。
"""

from app.interfaces.ws.performance import stream_metrics


class FakeMetrics:
    """PerformanceMetrics 最小替身（handler 仅读属性）。"""

    def __init__(self, ts: float = 1726000000.5) -> None:
        self.ts = ts
        self.cpu_percent = 87.2
        self.total_memory_mb = 4096.0
        self.used_memory_mb = 2048.0
        self.fps = 59.0
        self.jank_count = 0
        self.current_activity = "com.example/.MainActivity"
        self.top_package = "com.example"
        self.fps_fresh = True


class FakePerfService:
    def __init__(self, metrics_list, alerts=None) -> None:
        self._metrics = metrics_list
        self._alerts = alerts if alerts is not None else []
        self.alert_calls: list[tuple[str, float]] = []

    async def stream_metrics(self, device_id: str):
        for m in self._metrics:
            yield m

    def get_alerts(self, device_id: str, sample_ts: float) -> list:
        self.alert_calls.append((device_id, sample_ts))
        return self._alerts


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


async def test_ws_message_includes_alerts_key():
    """样本消息携带活动告警快照；以样本 ts 取快照；fps_fresh 不透出（方案 §4.4）。"""
    alert = {
        "id": "cpu_percent",
        "value": 87.2,
        "threshold": 80.0,
        "direction": "above",
        "since": 1726000000.0,
        "notify": True,
    }
    svc = FakePerfService([FakeMetrics()], alerts=[alert])
    ws = FakeWebSocket()

    await stream_metrics(ws, "dev-1", service=svc)

    assert ws.accepted is True
    assert len(ws.sent) == 1
    msg = ws.sent[0]
    assert msg["alerts"] == [alert]
    assert msg["cpu_percent"] == 87.2                  # 既有字段不变（向后兼容）
    assert svc.alert_calls == [("dev-1", 1726000000.5)]
    assert "fps_fresh" not in msg                      # 服务端判定字段不透出


async def test_ws_message_alerts_empty_without_alerts():
    """无活动告警时为 []（既有前端忽略多余键）。"""
    svc = FakePerfService([FakeMetrics()])
    ws = FakeWebSocket()

    await stream_metrics(ws, "dev-1", service=svc)

    assert ws.sent[0]["alerts"] == []
```

`tests/unit/test_http_network.py` 修改：

1. `FakeNetworkService.__init__` 加参数与记录：

```python
    def __init__(
        self,
        stats: NetworkStats | None = None,
        connections: list[NetworkConnection] | None = None,
        alerts: list[dict[str, Any]] | None = None,
    ) -> None:
        self.stats = stats if stats is not None else make_stats()
        self.connections = connections if connections is not None else []
        self.alerts = alerts if alerts is not None else []
        self.stats_calls: list[str] = []
        self.conn_calls: list[str] = []
        self.alert_calls: list[tuple[str, float]] = []
```

并加方法：

```python
    def get_alerts(self, device_id: str, sample_ts: float) -> list[dict[str, Any]]:
        self.alert_calls.append((device_id, sample_ts))
        return self.alerts
```

2. `test_stats_snapshot_fields` 期望字典补 `"alerts": []`：

```python
    assert response.json() == {
        "ts": 1726000000.5,
        "rx_bytes": 111111,
        "tx_bytes": 222222,
        "rx_rate_kbps": 12.5,
        "tx_rate_kbps": 3.5,
        "active_connections": 2,
        "wifi_connected": True,
        "wifi_ssid": "MyWiFi",
        "alerts": [],
    }
```

3. 新增用例：

```python
def test_stats_includes_alerts_snapshot() -> None:
    """响应内嵌活动告警快照，以 stats.ts 取快照（方案 24 §4.4）。"""
    alert = {
        "id": "rx_rate_kbps",
        "value": 900.0,
        "threshold": 800.0,
        "direction": "above",
        "since": 1726000000.0,
        "notify": True,
    }
    fake = FakeNetworkService(alerts=[alert])
    with override(get_network_service, fake):
        body = make_client().get(f"/api/network/{DEV}/stats").json()

    assert body["alerts"] == [alert]
    assert fake.alert_calls == [(DEV, 1726000000.5)]
```

- [ ] **Step 10: 运行确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_ws_performance.py tests/unit/test_http_network.py -q`
Expected: FAIL — WS 消息无 `alerts` 键（KeyError）/ HTTP 响应无 `alerts`（字典不等）。

- [ ] **Step 11: 实现接口层**

`backend/app/interfaces/ws/performance.py` 的 `data = {...}` 字典末尾（`"top_package": metrics.top_package,` 之后）加一行：

```python
                "alerts": service.get_alerts(device_id, metrics.ts),
```

`backend/app/interfaces/http/network.py` 的 `get_stats` 返回字典末尾（`"wifi_ssid": stats.wifi_ssid,` 之后）加一行：

```python
        "alerts": service.get_alerts(device_id, stats.ts),
```

- [ ] **Step 12: 运行确认通过 + 门禁 + 提交**

```bash
PYTHONPATH=backend:. python -m pytest tests/unit/test_ws_performance.py tests/unit/test_http_network.py -q
python -m ruff check backend/app && python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q
git add backend/app tests/
git commit -m "$(cat <<'EOF'
feat: 方案 24 T2 采样器 fps_fresh 标记 + 两服务告警挂点 + 接口内嵌下发

- sampler.py：_get_fps_and_jank 返回 (fps, jank, fresh) 三元组，
  仅实测分支 fresh=True；PerformanceMetrics.fps_fresh 不透出/不落库
- performance_service.py：AlertEngine 挂点（notify_window=max(3×interval,5)），
  _sampling_loop 先评估后推送；_release_device 单点释放；get_alerts 快照
- network_service.py：同形态挂点；释放落 finally 与 stop_monitoring 两处
- interfaces：WS perf 消息与 /api/network/{id}/stats 响应加 alerts 键
- 测试：test_ws_performance.py 新建（端点此前零覆盖）；test_http_network
  替身补 get_alerts 并同步精确期望；sampler/perf/network 服务用例扩展

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 前端共享模块与两视图接入

**Files:**
- Create: `frontend/src/services/alerts.ts`
- Create: `frontend/src/services/alerts.test.ts`
- Modify: `frontend/src/components/debug/PerfView.vue`
- Modify: `frontend/src/components/debug/PerfView.test.ts`
- Modify: `frontend/src/components/debug/NetworkView.vue`
- Modify: `frontend/src/components/debug/NetworkView.test.ts`

**Interfaces:**
- Consumes: 后端快照 JSON 形状（方案 §4.2：`{id, value, threshold, direction, since, notify}`）。
- Produces: `AlertItem`、`alertLabel(id)`、`extractAlerts(data)`、`createAlertNotifier()`（T4 打包验证依赖构建通过；无其他任务依赖）。

- [ ] **Step 1: 写失败测试 frontend/src/services/alerts.test.ts**

```typescript
/**
 * 告警共享模块测试（方案 24 §6 前端-1）
 * ======================================
 *
 * 覆盖：extractAlerts 防御式解析 / alertLabel 映射与兜底 /
 * createAlertNotifier（episode 去重、窗口内多帧只弹一次、换 since 再弹、列表清空重置、文案）
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const elMessage = vi.hoisted(() => ({ warning: vi.fn() }))
vi.mock('element-plus', () => ({ ElMessage: elMessage }))

import { alertLabel, createAlertNotifier, extractAlerts, type AlertItem } from './alerts'

function makeAlert(overrides: Partial<AlertItem> = {}): AlertItem {
  return {
    id: 'cpu_percent',
    value: 87.2,
    threshold: 80,
    direction: 'above',
    since: 1726000000.0,
    notify: true,
    ...overrides,
  }
}

beforeEach(() => {
  elMessage.warning.mockClear()
})

describe('extractAlerts', () => {
  it('非数组/脏字段 → []', () => {
    expect(extractAlerts(undefined)).toEqual([])
    expect(extractAlerts(null)).toEqual([])
    expect(extractAlerts('x')).toEqual([])
    expect(extractAlerts({ id: 'cpu_percent' })).toEqual([])
    expect(extractAlerts([{ id: 1, since: 2 }, null, {}])).toEqual([])
  })

  it('合法数组原样返回', () => {
    const alert = makeAlert()
    expect(extractAlerts([alert])).toEqual([alert])
  })
})

describe('alertLabel', () => {
  it('已知 id 映射中文，未知 id 兜底原样', () => {
    expect(alertLabel('cpu_percent')).toBe('CPU 使用率')
    expect(alertLabel('fps')).toBe('帧率')
    expect(alertLabel('custom_metric')).toBe('custom_metric')
  })
})

describe('createAlertNotifier', () => {
  it('窗口内多帧同 episode 只弹一次', () => {
    const notifier = createAlertNotifier()
    const alert = makeAlert()
    notifier.update([alert])
    notifier.update([alert])
    notifier.update([alert])
    expect(elMessage.warning).toHaveBeenCalledTimes(1)
  })

  it('换 since 的新 episode 再弹', () => {
    const notifier = createAlertNotifier()
    notifier.update([makeAlert()])
    notifier.update([makeAlert({ since: 1726000100.0 })])
    expect(elMessage.warning).toHaveBeenCalledTimes(2)
  })

  it('notify=false 不弹（仅徽标）', () => {
    const notifier = createAlertNotifier()
    notifier.update([makeAlert({ notify: false })])
    expect(elMessage.warning).not.toHaveBeenCalled()
  })

  it('活动列表清空后重置，同 key 再来会再弹', () => {
    const notifier = createAlertNotifier()
    const alert = makeAlert()
    notifier.update([alert])
    notifier.update([])
    notifier.update([alert])
    expect(elMessage.warning).toHaveBeenCalledTimes(2)
  })

  it('文案：above 为「超过阈值」、below 为「低于阈值」', () => {
    const notifier = createAlertNotifier()
    notifier.update([makeAlert()])
    notifier.update([makeAlert({ id: 'fps', direction: 'below', threshold: 30, value: 25.4, since: 1 })])

    const first = elMessage.warning.mock.calls[0][0] as { message: string; grouping: boolean }
    expect(first.message).toBe('CPU 使用率 87.2 超过阈值 80.0')
    expect(first.grouping).toBe(true)

    const second = elMessage.warning.mock.calls[1][0] as { message: string }
    expect(second.message).toBe('帧率 25.4 低于阈值 30.0')
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run src/services/alerts.test.ts`
Expected: FAIL — 模块 `./alerts` 不存在。

- [ ] **Step 3: 实现 frontend/src/services/alerts.ts**

```typescript
/**
 * 性能阈值告警共享模块（方案 24）
 * ================================
 *
 * 层：services（纯逻辑，无组件/Store 依赖）。
 *
 * 职责：
 *   - AlertItem 类型与后端快照 JSON 对齐（方案 §4.2）
 *   - extractAlerts：防御式解析（脏数据 → []，既有消息无 alerts 键安全）
 *   - alertLabel：指标 id → 中文标签（展示无关的引擎只发原始 id）
 *   - createAlertNotifier：notify=true 条目按 id:since(episode) 去重弹 toast；
 *     服务端降噪（迟滞+冷却）与客户端去重两层职责分明
 */
import { ElMessage } from 'element-plus'

export interface AlertItem {
  id: string
  value: number | null
  threshold: number
  direction: 'above' | 'below'
  since: number
  notify?: boolean
}

const ALERT_LABELS: Record<string, string> = {
  cpu_percent: 'CPU 使用率',
  memory_percent: '内存使用率',
  fps: '帧率',
  rx_rate_kbps: '接收速率',
  tx_rate_kbps: '发送速率',
}

export function alertLabel(id: string): string {
  return ALERT_LABELS[id] ?? id
}

export function extractAlerts(data: unknown): AlertItem[] {
  if (!Array.isArray(data)) return []
  return data.filter(
    (item): item is AlertItem =>
      !!item &&
      typeof item === 'object' &&
      typeof (item as AlertItem).id === 'string' &&
      typeof (item as AlertItem).since === 'number',
  )
}

function formatAlert(item: AlertItem): string {
  const label = alertLabel(item.id)
  const value = typeof item.value === 'number' ? item.value.toFixed(1) : '-'
  const threshold = item.threshold.toFixed(1)
  return item.direction === 'above'
    ? `${label} ${value} 超过阈值 ${threshold}`
    : `${label} ${value} 低于阈值 ${threshold}`
}

/** 每个视图实例创建一个：episode 去重状态随视图生命周期（多设备多实例互不串扰）。 */
export function createAlertNotifier(): { update(alerts: AlertItem[]): void } {
  const notified = new Set<string>()

  function update(alerts: AlertItem[]): void {
    const activeKeys = new Set<string>()
    for (const item of alerts) {
      const key = `${item.id}:${item.since}`
      activeKeys.add(key)
      if (item.notify === true && !notified.has(key)) {
        notified.add(key)
        ElMessage.warning({ message: formatAlert(item), grouping: true })
      }
    }
    // 已通知集随当前活动列表裁剪（episode 解除后释放记录；列表清空整体重置）
    for (const key of notified) {
      if (!activeKeys.has(key)) notified.delete(key)
    }
  }

  return { update }
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run src/services/alerts.test.ts`
Expected: PASS。

- [ ] **Step 5: 写 PerfView 失败测试（追加用例）**

在 `frontend/src/components/debug/PerfView.test.ts` 的 `describe('PerfView', ...)` 内追加：

```typescript
  it('alerts 消息渲染徽标条并驱动卡片警示色；同 episode 只弹一次 toast', async () => {
    const wrapper = mountView()
    const ws = latestWs()

    const alerts = [
      { id: 'cpu_percent', value: 87.2, threshold: 80, direction: 'above', since: 1, notify: true },
      { id: 'memory_percent', value: 93.5, threshold: 90, direction: 'above', since: 2, notify: false },
      { id: 'fps', value: 25.4, threshold: 30, direction: 'below', since: 3, notify: true },
    ]
    ws.messageHandler!(JSON.stringify({ ...metrics, alerts }))
    await nextTick()

    // 徽标条：3 枚 chip
    const chips = wrapper.findAll('.alert-chip')
    expect(chips).toHaveLength(3)
    expect(chips[0].text()).toContain('CPU 使用率')
    expect(chips[0].text()).toContain('87.2')

    // 卡片警示色由 alerts 驱动（cpu/内存/fps 三卡均 warning）
    const values = wrapper.findAll('.metric-value')
    expect(values[0].classes()).toContain('warning')
    expect(values[1].classes()).toContain('warning')
    expect(values[2].classes()).toContain('warning')

    // notify=true 两枚各弹一次；notify=false 不弹
    expect(elMessage.warning).toHaveBeenCalledTimes(2)

    // 同 episode 第二帧：不再弹、徽标仍在
    ws.messageHandler!(JSON.stringify({ ...metrics, alerts }))
    await nextTick()
    expect(elMessage.warning).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.alert-chip')).toHaveLength(3)

    // 告警解除（空数组）：徽标消失、卡片警示色消失
    ws.messageHandler!(JSON.stringify({ ...metrics, alerts: [] }))
    await nextTick()
    expect(wrapper.findAll('.alert-chip')).toHaveLength(0)
    expect(wrapper.findAll('.metric-value')[0].classes()).not.toContain('warning')
  })

  it('无 alerts 键的消息不渲染徽标、不弹 toast（既有推送防御式兼容）', async () => {
    const wrapper = mountView()
    const ws = latestWs()

    ws.messageHandler!(JSON.stringify(metrics))
    await nextTick()

    expect(wrapper.findAll('.alert-chip')).toHaveLength(0)
    expect(elMessage.warning).not.toHaveBeenCalled()
  })
```

- [ ] **Step 6: 写 NetworkView 失败测试（追加用例）**

在 `frontend/src/components/debug/NetworkView.test.ts` 的 describe 内追加（mockApi.getNetworkStats 的 mockResolvedValue 用既有 `stats` 对象展开；timer 推进沿用该文件既有范式：用例首行 `vi.useFakeTimers()`、`await vi.advanceTimersByTimeAsync(2000)`、`afterEach` 已 `vi.useRealTimers()`）：

```typescript
  it('轮询响应含 alerts → 渲染徽标条并弹一次 toast；同 episode 下一轮不再弹', async () => {
    vi.useFakeTimers()
    mockApi.getNetworkStats.mockResolvedValue({
      ...stats,
      alerts: [
        { id: 'rx_rate_kbps', value: 900, threshold: 800, direction: 'above', since: 1, notify: true },
      ],
    })
    mockApi.getNetworkConnections.mockResolvedValue({ connections: [] })
    const wrapper = mountView()
    await flushPromises()

    const chips = wrapper.findAll('.alert-chip')
    expect(chips).toHaveLength(1)
    expect(chips[0].text()).toContain('接收速率')
    expect(elMessage.warning).toHaveBeenCalledTimes(1)

    // 下一轮轮询（2s）：同一 episode（id:since 未变）→ 不再弹
    await vi.advanceTimersByTimeAsync(2000)
    expect(elMessage.warning).toHaveBeenCalledTimes(1)
  })

  it('无 alerts 键的响应不渲染徽标', async () => {
    mockApi.getNetworkStats.mockResolvedValue({ ...stats })
    mockApi.getNetworkConnections.mockResolvedValue({ connections: [] })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.findAll('.alert-chip')).toHaveLength(0)
    expect(elMessage.warning).not.toHaveBeenCalled()
  })
```

注意：本用例只断言「同 episode 不再弹」的集成行为；`createAlertNotifier` 的去重/裁剪细节已由 `alerts.test.ts` 单测锁定，此处不重复。

- [ ] **Step 7: 运行确认失败**

Run: `cd frontend && npx vitest run src/components/debug/PerfView.test.ts src/components/debug/NetworkView.test.ts`
Expected: FAIL — 无 `.alert-chip`。

- [ ] **Step 8: 实现 PerfView.vue**

1. 模板：`<div class="metrics-grid">` 之前插入徽标条：

```html
    <!-- 活动告警徽标条（方案 24）：面板开着也能一眼看到异常 -->
    <div v-if="activeAlerts.length" class="alert-bar">
      <span v-for="a in activeAlerts" :key="a.id" class="alert-chip">
        {{ alertLabel(a.id) }} {{ a.value !== null ? a.value.toFixed(1) : '-' }}
      </span>
    </div>
```

2. CPU 卡片 warning 表达式（约 :57）改为：

```html
        <div class="metric-value" :class="{ warning: hasAlert('cpu_percent') }">
```

3. 内存卡片值行（约 :67）改为：

```html
        <div class="metric-value" :class="{ warning: hasAlert('memory_percent') }">
```

4. FPS 卡片（约 :80）改为：

```html
        <div class="metric-value" :class="{ warning: hasAlert('fps') }">
```

5. script：
   - import 区加：`import { alertLabel, createAlertNotifier, extractAlerts, type AlertItem } from '@/services/alerts'`
   - `interface PerfMetrics` 末尾加 `alerts?: AlertItem[]`
   - `const wsConnected = ref(false)` 附近加：

```typescript
// 活动告警（方案 24）：WS 每帧携带当前快照，驱动徽标条与卡片警示色
const activeAlerts = ref<AlertItem[]>([])
const alertNotifier = createAlertNotifier()

function hasAlert(id: string): boolean {
  return activeAlerts.value.some((a) => a.id === id)
}
```

   - WS 处理（约 :401-415）在 `const metrics = JSON.parse(data) as PerfMetrics` 之后插：

```typescript
        activeAlerts.value = extractAlerts(metrics.alerts)
        alertNotifier.update(activeAlerts.value)
```

6. 样式：`.metric-value.warning` 规则旁加：

```css
.alert-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.alert-chip {
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  color: #e6a23c;
  background: #fdf6ec;
  border: 1px solid #f5dab1;
}
```

- [ ] **Step 9: 实现 NetworkView.vue**

1. 模板：工具栏 `</div>`（含 REC 指示器的 toolbar 闭合）之后插入：

```html
    <!-- 活动告警徽标条（方案 24） -->
    <div v-if="activeAlerts.length" class="alert-bar">
      <span v-for="a in activeAlerts" :key="a.id" class="alert-chip">
        {{ alertLabel(a.id) }} {{ a.value !== null ? a.value.toFixed(1) : '-' }}
      </span>
    </div>
```

2. `NetworkStats` 接口（约 :140-149）加 `alerts?: AlertItem[]`。
3. script import 加同款 alerts 模块导入；组件内加 `activeAlerts`/`alertNotifier`（同 PerfView 形态，不需要 `hasAlert`）。
4. `fetchData`（约 :295-322）在 `currentStats.value = stats` 之后插：

```typescript
    activeAlerts.value = extractAlerts(stats.alerts)
    alertNotifier.update(activeAlerts.value)
```

5. 样式加同款 `.alert-bar` / `.alert-chip`。

- [ ] **Step 10: 运行前端测试与门禁**

```bash
cd frontend && npx vitest run src/services/alerts.test.ts src/components/debug/PerfView.test.ts src/components/debug/NetworkView.test.ts
npm run lint && npx vitest run --coverage && npm run build
```

Expected: 全部 PASS（含 `src/services/**` 80% 覆盖率门禁）；构建（vue-tsc）通过。

- [ ] **Step 11: 提交**

```bash
git add frontend/src/services/alerts.ts frontend/src/services/alerts.test.ts frontend/src/components/debug/PerfView.vue frontend/src/components/debug/PerfView.test.ts frontend/src/components/debug/NetworkView.vue frontend/src/components/debug/NetworkView.test.ts
git commit -m "$(cat <<'EOF'
feat: 方案 24 T3 前端告警共享模块与两视图接入

- services/alerts.ts：AlertItem/extractAlerts（防御式）/alertLabel/
  createAlertNotifier（id:since episode 去重 + 列表裁剪重置）
- PerfView：徽标条 + 卡片警示色改由 alerts 驱动（移除 >80/<30 硬编码，
  内存卡片纳入）；WS 每帧更新快照与通知
- NetworkView：轮询响应 alerts → 徽标条 + 通知（响应即状态，重连安全）
- 测试：alerts.test.ts 新建；两视图测试覆盖徽标/警示色/去重/无键防御

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 全量门禁、文档回写与打包产物重建

**Files:**
- Modify: `方案/08-性能监控.md`（状态头等 ⏳ 告警条目）
- Modify: `README.md`（模块表性能监控行，约 :87）
- Modify: `方案/进度追踪.md`（总体进度表 + 模块详细状态 + 更新日志）
- Modify: `方案/24-性能阈值告警方案.md`（状态头 → 已实施）
- Build: `bash scripts/build_exe.sh` → `dist/OpenScrcpy-win64-0.1.0.zip`

**Interfaces:**
- Consumes: T1-T3 全部代码提交。
- Produces: 绿色版 zip 产物（T5 运行时验证对象）。

- [ ] **Step 1: 全量门禁（后端 + 前端）**

```bash
python -m ruff check backend/app && python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
cd frontend && npm run lint && npx vitest run --coverage && npm run build
```

Expected: 全绿。任何失败必须先修复（回到对应任务修补，不在本任务内夹带实现改动——若确需修，单独提交说明）。

- [ ] **Step 2: 文档回写**

1. `方案/08-性能监控.md` 状态头：将 `- ⏳ 性能告警——可选（阈值告警未实施）` 替换为：

```markdown
- ✅ 阈值告警（方案 24，2026-09-28）：后端判定（连续 N 次 + 迟滞余量 + 冷却抑制）
  + WS/轮询内嵌 alerts 快照 + 前端 toast/徽标条；不落库（内存态随监控释放）
```

并通读该文件，将验收标准/交付物节中其余「性能告警」⏳ 条目同步标记为 ✅（引用方案 24）。

2. `README.md` 模块表性能监控行（约 :87）：

```markdown
| Week 5-6 | 性能监控 | [08-性能监控.md](方案/08-性能监控.md) | ✅ 98%（含网络监控 + 阈值告警（方案 24）） |
```

3. `方案/进度追踪.md`：
   - 总体进度表性能监控行备注 `余性能告警可选` → `含阈值告警（方案 24）`，完成度 95% → 98%。
   - 「#### 性能监控（95%）」标题改「（98%）」，条目区末尾（`- ✅ FPS 应用切换负值修复（2026-09-11）` 之后）加：

```markdown
- ✅ 阈值告警（方案 24，2026-09-28）：AlertEngine 连续/迟滞/冷却判定 + WS/轮询内嵌 alerts + 前端 toast/徽标条（不落库）
```

   - 更新日志尾部追加（编号 = 当前最后一条「第三十二次」自增为「第三十三次」；写前先读尾部核对）：

```markdown
### 2026-09-28（第三十三次更新 - 性能阈值告警方案 24 实施）

> 背景：方案 08 遗留「性能告警」可选项实施。SDD 四任务执行（T1 配置与评估器 → T2 采样器/服务挂点/接口 → T3 前端 → T4 门禁与打包）。

- ✅ **T1**：`metrics.thresholds` 嵌套配置（cpu 80 / mem 90 / fps 30；网络速率默认禁用）+ `AlertEngine` 状态机（连续 N 次触发 + 迟滞余量重置 + 冷却抑制 + notify 窗口匹配 + fresh/None 无效样本跳过 + sync 热重载）
- ✅ **T2**：采样器 `fps_fresh` 标记（沿用/空窗不计入 fps 判定）；两服务挂点（perf `_release_device` 单点 / network finally+stop 两处释放）；WS perf 与 network stats 内嵌 `alerts` 快照
- ✅ **T3**：前端 `alerts.ts`（防御式解析 + id:since episode 去重通知）+ PerfView 徽标条与卡片警示色 alerts 化（移除硬编码 80/30）+ NetworkView 轮询接入
- ✅ **T4**：全门禁 + 文档回写 + zip 重建（打包产物运行时验证见下条）
- 门禁：后端 pytest（cov ≥80%）、ruff/mypy 全绿；前端 vitest（services 层 80% 门禁）、eslint/build 全绿
```

   尾部 `**最后更新**` 行同步为 `2026-09-28（第三十三次更新 - 性能阈值告警方案 24 实施）`。

4. `方案/24-性能阈值告警方案.md` 状态头首行改为：

```markdown
> **状态**：已实施（2026-09-28，SDD T1~T4 完成；打包产物运行时验证见进度追踪）。
```

- [ ] **Step 3: 提交文档**

```bash
git add 方案/08-性能监控.md README.md 方案/进度追踪.md 方案/24-性能阈值告警方案.md
git commit -m "$(cat <<'EOF'
docs: 方案 24 T4 文档回写（08 状态头 / README 模块表 / 进度追踪第三十三次更新）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: 重建打包产物**

```bash
bash scripts/build_exe.sh
```

（构建链路：fetch_tools → 前端 npm ci+build → 干净 venv + PyInstaller → zip；耗时约 10-15 分钟，用后台运行监控输出。）

预期：`dist/OpenScrcpy-win64-0.1.0.zip` 生成并打印 SHA256；zip 体积与方案 15 基线 19.0MiB 相当（零新依赖 → 无显著变化）。

- [ ] **Step 5: 记录构建结论**

将 zip 路径 + SHA256 + 体积写入 T4 报告（不新增文档文件）；若有异常（构建失败/体积暴涨）→ 停止并上报，不得绕过。

---

### Task 5: 打包产物运行时验证（绿色 exe 实测）

**前提**：T1-T4 全部通过；dev 后端仍在 0.0.0.0:8765 运行（不得干扰）。

**说明**：本任务为运行时验证（无代码产出），由控制器在 T4 报告评审通过后执行；结论写入 SDD ledger 与进度追踪（若需回写文档则补一次 docs 提交）。

- [ ] **Step 1: 启动前基线**

```bash
ls dist/OpenScrcpy/OpenScrcpy.exe
netstat -ano | grep -E "8765|8766" || true     # 记录既有占用（预期 dev 占 8765）
rm -rf dist/OpenScrcpy/data dist/OpenScrcpy/logs 2>/dev/null || true  # 清残留运行数据（zip 本就不含）
```

- [ ] **Step 2: 启动 exe 并等待就绪**

```bash
cd dist/OpenScrcpy && ./OpenScrcpy.exe &       # 后台启动（会自动打开浏览器）
# 轮询就绪（最多 30s）：
for i in $(seq 1 30); do [ -f data/port.txt ] && break; sleep 1; done
cat data/port.txt
```

预期：`data/port.txt` 内容为 **8766**（dev 占 8765 → D1 修复生效回退；若 dev 已停则为 8765）。

- [ ] **Step 3: 服务功能探测**

```bash
PORT=$(cat dist/OpenScrcpy/data/port.txt)
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$PORT/health"     # 预期 200
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$PORT/"           # 预期 200（前端静态页）
curl -s "http://127.0.0.1:$PORT/api/network/nonexistent/stats" | head -c 300 # 观察响应形状（无设备时错误/空态均可，不得 500 崩溃）
```

若 dev 与 exe 端口确认区分正确，再验证「与 dev 互不干扰」：`curl http://127.0.0.1:8765/health` 仍由 dev 应答（PID 仍为 dev 进程）。

- [ ] **Step 4: 日志与告警功能检查**

```bash
grep -iE "error|traceback|exception" dist/OpenScrcpy/logs/openscrcpy.log | head -20   # 预期零 error
```

再验证新配置加载与端点：`metrics.thresholds` 在打包版 base.yaml 内（spec datas 注入 config 目录）——同 Step 3 的 health 探测已隐含启动成功（配置解析失败会启动崩溃）；如有真机在线（`adb devices`），额外连一台设备打开 perf 走 WS（浏览器人工确认无 JS 错误、标尺正常），无真机则以「服务与页面正常 + 日志零 error」为通过口径并在报告中注明设备条件。

- [ ] **Step 5: 优雅退出与端口释放**

```bash
PORT=$(cat dist/OpenScrcpy/data/port.txt)
curl -s -X POST "http://127.0.0.1:$PORT/api/system/shutdown"
sleep 3
netstat -ano | grep ":$PORT" || echo "port released"
```

预期：退出响应 accepted；端口释放；进程消失（`tasklist | grep -i openscrcpy` 空）。

- [ ] **Step 6: 二启单开复验（回归）**

再次启动 exe → 读 `data/port.txt` → 浏览器指向已运行实例 → 进程数仍为 1（验证方案 23 行为未被本方案回归）；随后再次 shutdown 清理。

- [ ] **Step 7: 验证结论上位**

将结论（端口回退实证 / health / 日志 / 退出 / 二启）+ zip SHA256 追加到 `方案/进度追踪.md` 第三十三次更新条目（T4 的 Step 2 已建条目，此处补「打包产物运行时验证通过」小节点）——若需改动已提交文档则单独一次 docs 提交。

---

## 自检记录（Self-Review）

- **Spec 覆盖**：方案 §9 T1-T4 全部展开；用户补充要求「实施后打包可用」= T4（构建）+ T5（运行时验证）；§7 验收标准的自动化部分落在 T1-T3 测试，手工部分落在 T5。
- **占位符扫描**：无 TBD/TODO/占位行；NetworkView 测试的 timer 推进已按既有文件范式写实（`vi.useFakeTimers()` + `await vi.advanceTimersByTimeAsync(2000)`，afterEach 已 `vi.useRealTimers()`）；network 追加测试已改用文件内既有 `wait_until`/`fast_loop`（勿自拟轮询）；perf 追加测试已明确需补 `import time`。
- **类型一致性**：`AlertRule`/`AlertEngine`/`build_rules`/`get_alerts`/`AlertItem`/`extractAlerts`/`createAlertNotifier`/`alertLabel`/`fps_fresh` 在任务间引用签名一致；快照 JSON 形状 §4.2 与 T3 前端 `AlertItem` 字段一致。
- **Review Focus**：5 条各自有测试（T1 Step 5 测试 4/1/6；T2 Step 5/9 释放与接口用例；T3 Step 1/5 防御式用例）。