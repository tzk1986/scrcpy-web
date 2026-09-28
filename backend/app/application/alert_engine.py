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


# 无效规则告警去重（T2 补充项）：采样循环每 tick 热重载式调用 build_rules，
# 误配置下按「无效签名变化」告警，避免按采样频率刷日志
_last_invalid_signature: tuple[tuple[str, float | None, float | None], ...] | None = None


def build_rules(metric_ids: tuple[str, ...] = METRIC_IDS) -> list[AlertRule]:
    """从配置构建规则集（只产出 enabled 且方向合法的规则，方案 24 §4.1）。"""
    global _last_invalid_signature
    thresholds = settings().metrics.thresholds
    rules: list[AlertRule] = []
    invalid: list[tuple[str, float | None, float | None]] = []
    for mid in metric_ids:
        cfg = getattr(thresholds, mid)
        if not cfg.enabled:
            continue
        if (cfg.above is None) == (cfg.below is None):
            invalid.append((mid, cfg.above, cfg.below))
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
    signature = tuple(invalid)
    if not invalid:
        _last_invalid_signature = None  # 本次全部有效：下次无效可再告警
    elif signature != _last_invalid_signature:
        _last_invalid_signature = signature
        for mid, above, below in invalid:
            logger.warning("alert_rule_invalid", metric=mid, above=above, below=below)
    return rules
