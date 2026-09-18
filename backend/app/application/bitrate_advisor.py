"""
码率自适应决策器
==================

层：应用层。

纯逻辑，无 IO、无时钟——当前时间由调用方注入，便于单测。

背景（2026-09-17 调研）：
    scrcpy v4.1（含 v2.4）上游协议没有运行中改码率的控制消息
    （ControlMessage 类型表中无 CHANGE_STREAM_PARAMETERS，仅
    RESET_VIDEO=17 触发重出关键帧）。因此自适应码率只能通过
    "按档位重启 scrcpy-server 编码器" 实现，代价是每次切换有
    约 1-3s 黑屏。本决策器用迟滞 + 冷却把重启压到很少发生：
      - 持续低 fps（客户端 WebCodecs 实收帧率）→ 降一档
      - 恢复高 fps 且超过升档冷却 → 升一档（上限为起始档）

用法：
    advisor = BitrateAdvisor(AdvisorConfig(...))
    advisor.add_sample(now, client_fps)   # 客户端 1Hz 上报
    new_bps = advisor.decide(now)         # 非 None 表示应切到该码率
"""

from collections import deque
from dataclasses import dataclass


def parse_bit_rate(value: str | int) -> int:
    """解析码率配置值："4M"→4000000、"512K"→512000、int 原样返回。"""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s.upper().endswith("M"):
        return int(float(s[:-1]) * 1_000_000)
    if s.upper().endswith("K"):
        return int(float(s[:-1]) * 1_000)
    return int(s)


@dataclass(frozen=True)
class AdvisorConfig:
    tiers_bps: tuple[int, ...]   # 降序档位阶梯，如 (8M,4M,2M,1M)
    target_fps: float
    start_bps: int               # 期望起始码率（升档上限 = 起始档）
    bad_ratio: float = 0.7       # fps < bad_ratio*target 记为坏样本
    good_ratio: float = 0.95     # fps >= good_ratio*target 记为好样本
    window: int = 8              # 滑动窗口样本数
    bad_min: int = 6             # 窗口内坏样本达到则降档
    good_min: int = 8            # 窗口内好样本达到则可能升档
    min_interval_s: float = 30.0  # 两次切换最小间隔
    up_extra_s: float = 90.0     # 升档在 min_interval 之外额外冷却


class BitrateAdvisor:
    """单路流的码率档位决策器。"""

    def __init__(self, cfg: AdvisorConfig):
        self._cfg = cfg
        tiers = tuple(cfg.tiers_bps)
        if not tiers:
            raise ValueError("tiers_bps 不能为空")
        self._tiers = tiers
        # 起始档 = 不超过 start_bps 的最大档；若全部超出则取最低档
        start_idx = next(
            (i for i, bps in enumerate(tiers) if bps <= cfg.start_bps),
            len(tiers) - 1,
        )
        self._start_idx = start_idx
        self._idx = start_idx
        self._samples: deque[float] = deque(maxlen=cfg.window)
        self._last_switch: float | None = None

    @property
    def current_bps(self) -> int:
        return self._tiers[self._idx]

    def add_sample(self, now: float, fps: float) -> None:
        self._samples.append(fps)

    def decide(self, now: float) -> int | None:
        """返回应切换到的新码率（bps），无需动作返回 None。"""
        cfg = self._cfg
        if len(self._samples) < cfg.window:
            return None
        if (
            self._last_switch is not None
            and now - self._last_switch < cfg.min_interval_s
        ):
            return None

        bad_line = cfg.bad_ratio * cfg.target_fps
        good_line = cfg.good_ratio * cfg.target_fps
        bad = sum(1 for f in self._samples if f < bad_line)
        good = sum(1 for f in self._samples if f >= good_line)

        if bad >= cfg.bad_min and self._idx < len(self._tiers) - 1:
            return self._switch(self._idx + 1, now)
        if (
            good >= cfg.good_min
            and self._idx > self._start_idx
            and self._last_switch is not None
            and now - self._last_switch >= cfg.min_interval_s + cfg.up_extra_s
        ):
            return self._switch(self._idx - 1, now)
        return None

    def _switch(self, new_idx: int, now: float) -> int:
        self._idx = new_idx
        self._last_switch = now
        self._samples.clear()
        return self.current_bps
