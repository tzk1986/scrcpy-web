"""
码率自适应决策器测试
=====================

BitrateAdvisor：基于客户端上报 fps 的迟滞决策（纯逻辑，时间由调用方注入）。

覆盖：
    - 起始档位选择（不超过配置码率的最大档）
    - 持续低 fps 逐级降档，最低档不再降
    - 冷却期内不切换
    - 降档后 fps 恢复、超过升档冷却才升回，且上限为起始档
    - 正常/混合样本不动作
    - parse_bit_rate 解析
"""

from app.application.bitrate_advisor import BitrateAdvisor, AdvisorConfig, parse_bit_rate

TIERS = (8_000_000, 4_000_000, 2_000_000, 1_000_000)


def make_cfg(start=4_000_000, **kw):
    defaults = dict(
        tiers_bps=TIERS,
        target_fps=30,
        start_bps=start,
        bad_ratio=0.7,
        good_ratio=0.95,
        window=8,
        bad_min=6,
        good_min=8,
        min_interval_s=30.0,
        up_extra_s=90.0,
    )
    defaults.update(kw)
    return AdvisorConfig(**defaults)


def feed(advisor, now0, fps, n, step=1.0):
    for i in range(n):
        advisor.add_sample(now0 + i * step, fps)
    return now0 + (n - 1) * step


class TestParseBitRate:
    def test_suffixes(self):
        assert parse_bit_rate("4M") == 4_000_000
        assert parse_bit_rate("512K") == 512_000
        assert parse_bit_rate("8000000") == 8_000_000
        assert parse_bit_rate(2_000_000) == 2_000_000


class TestInitialTier:
    def test_start_picks_largest_tier_not_exceeding(self):
        a = BitrateAdvisor(make_cfg(start=4_000_000))
        assert a.current_bps == 4_000_000

    def test_start_5m_falls_back_to_4m(self):
        a = BitrateAdvisor(make_cfg(start=5_000_000))
        assert a.current_bps == 4_000_000

    def test_start_below_lowest_tier_clamps_to_lowest(self):
        a = BitrateAdvisor(make_cfg(start=500_000))
        assert a.current_bps == 1_000_000


class TestDowngrade:
    def test_sustained_low_fps_steps_down_once(self):
        a = BitrateAdvisor(make_cfg())
        feed(a, 0.0, 10, 8)
        assert a.decide(9.0) == 2_000_000
        assert a.current_bps == 2_000_000

    def test_mixed_low_not_enough(self):
        a = BitrateAdvisor(make_cfg())
        fps_seq = [10] * 5 + [30] * 3
        t = 0.0
        for f in fps_seq:
            a.add_sample(t, f)
            t += 1.0
        assert a.decide(t) is None
        assert a.current_bps == 4_000_000

    def test_ineffective_downgrade_rolls_back(self):
        """降档未改善 → 回弹 + 冷静期，取代旧「链式降档」（方案 21）。"""
        a = BitrateAdvisor(make_cfg())
        t = feed(a, 0.0, 10, 8)
        assert a.decide(t + 1) == 2_000_000
        # 冷却不足不复核也不切换（19s < min_interval 30s）
        feed(a, 20.0, 5, 8)
        assert a.decide(28.0) is None
        assert a.current_bps == 2_000_000
        # 复核：降档后坏样本未下降 → 回弹 4M（旧行为是继续降 1M）
        assert a.decide(40.0) == 4_000_000

    def test_lowest_tier_stays(self):
        a = BitrateAdvisor(make_cfg(start=1_000_000))
        feed(a, 0.0, 5, 8)
        assert a.decide(9.0) is None
        assert a.current_bps == 1_000_000

    def test_fewer_than_window_no_action(self):
        a = BitrateAdvisor(make_cfg())
        feed(a, 0.0, 10, 7)
        assert a.decide(8.0) is None


class TestUpgrade:
    def test_recovers_to_start_tier_and_caps(self):
        a = BitrateAdvisor(make_cfg())
        t = feed(a, 0.0, 10, 8)
        assert a.decide(t + 1) == 2_000_000
        # 立即恢复高 fps：升档需额外冷却（min_interval+up_extra），先不动
        feed(a, 15.0, 30, 8)
        assert a.decide(24.0) is None
        # 超过升档冷却后升回 4M（起始档，不升到 8M）
        feed(a, 130.0, 30, 8)
        assert a.decide(140.0) == 4_000_000
        feed(a, 300.0, 30, 8)
        assert a.decide(310.0) is None
        assert a.current_bps == 4_000_000


class TestDowngradeReview:
    """方案 21 候选 a：降档有效性复核——有效保留 / 无效回弹 + 冷静期。"""

    def test_static_fps_rolls_back_and_cooldowns(self):
        a = BitrateAdvisor(make_cfg())
        feed(a, 0.0, 3.74, 8)                # 静止 3.74fps：8/8 坏样本
        assert a.decide(9.0) == 2_000_000    # 降档 4M→2M，记录 bad_old=8
        # 重启后新窗口仍静止 → 复核无效 → 回弹 4M + 冷静期
        feed(a, 20.0, 3.74, 8)
        assert a.decide(39.0) == 4_000_000
        assert a.current_bps == 4_000_000
        # 冷静期（600s）内持续低 fps 也不再降档（无横跳）
        feed(a, 100.0, 3.74, 8)
        assert a.decide(109.0) is None
        assert a.current_bps == 4_000_000
        # 冷静期结束后恢复自动降档能力（每轮降档都会重新复核）
        feed(a, 700.0, 3.74, 8)
        assert a.decide(709.0) == 2_000_000

    def test_real_congestion_improvement_keeps_tier(self):
        a = BitrateAdvisor(make_cfg())
        feed(a, 0.0, 10, 8)                  # 真实拥塞：10fps 坏样本
        assert a.decide(9.0) == 2_000_000
        feed(a, 20.0, 30, 8)                 # 降档后显著改善
        assert a.decide(39.0) is None        # 复核有效：保留 2M，不回弹
        assert a.current_bps == 2_000_000

    def test_review_waits_for_full_window(self):
        a = BitrateAdvisor(make_cfg())
        feed(a, 0.0, 10, 8)
        assert a.decide(9.0) == 2_000_000
        feed(a, 20.0, 3.74, 7)               # 新窗口未填满
        assert a.decide(50.0) is None        # 不复核、不切换
        assert a.current_bps == 2_000_000

    def test_boundary_effective_at_half_bad(self):
        # bad_old=6（恰好触发降档的最小坏样本数）时，新窗 bad≤3 判有效
        a = BitrateAdvisor(make_cfg())
        fps_seq = [10] * 6 + [25] * 2        # 6 坏 + 2 中（21≤25<28.5）
        t = 0.0
        for f in fps_seq:
            a.add_sample(t, f)
            t += 1.0
        assert a.decide(9.0) == 2_000_000
        feed(a, 20.0, 25, 8)
        assert a.decide(39.0) is None        # 复核有效：保留 2M
        assert a.current_bps == 2_000_000

    def test_boundary_rollback_at_more_than_half_bad(self):
        # bad_old=6，新窗 bad=4 > 3 → 判无效回弹
        a = BitrateAdvisor(make_cfg())
        fps_seq = [10] * 6 + [25] * 2
        t = 0.0
        for f in fps_seq:
            a.add_sample(t, f)
            t += 1.0
        assert a.decide(9.0) == 2_000_000
        fps_seq2 = [10] * 4 + [25] * 4       # bad=4
        for f in fps_seq2:
            a.add_sample(t, f)
            t += 1.0
        assert a.decide(39.0) == 4_000_000   # 回弹 4M


class TestNoActionWhenHealthy:
    def test_good_fps_never_switches(self):
        a = BitrateAdvisor(make_cfg())
        for t in range(0, 60):
            a.add_sample(float(t), 30)
            assert a.decide(float(t) + 0.5) is None
        assert a.current_bps == 4_000_000
