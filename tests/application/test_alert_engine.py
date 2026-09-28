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
    metrics.thresholds.tx_rate_kbps.above = 0.0
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
