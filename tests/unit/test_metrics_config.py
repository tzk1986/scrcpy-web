"""
metrics 配置节测试（config/settings.py + config/base.yaml，方案 18 §3.5）
========================================================================

覆盖：
    - MetricsConfig 默认值与设计值一致（§3.5 表）
    - base.yaml 的 metrics 节被加载（配置分离不回归）
"""

from config.settings import get_settings, load_yaml_config


def test_metrics_config_defaults() -> None:
    """MetricsConfig 默认值逐项匹配方案 §3.5 设计表。"""
    metrics = get_settings().metrics

    assert metrics.buffer_size == 3600
    assert metrics.network_buffer_size == 1800
    assert metrics.network_interval == 2.0
    assert metrics.idle_ttl_seconds == 300
    assert metrics.lost_failures == 3
    assert metrics.recording is False
    assert metrics.retention_days == 3
    assert metrics.max_rows_total == 500000

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


def test_base_yaml_has_metrics_section() -> None:
    """base.yaml 显式声明 metrics 节（与默认值一致，防漂移）。"""
    section = load_yaml_config("base").get("metrics", {})

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


def test_thresholds_partial_override_parses() -> None:
    """YAML 局部覆盖经 Settings(**config) 解析为嵌套模型，未覆盖字段回落默认。"""
    from config.settings import Settings

    s = Settings(metrics={"thresholds": {"cpu_percent": {"above": 50.0}}})

    assert s.metrics.thresholds.cpu_percent.above == 50.0
    assert s.metrics.thresholds.cpu_percent.consecutive == 3   # 未覆盖字段回落默认
    assert s.metrics.thresholds.fps.below == 30.0              # 未覆盖指标回落默认


# ---------------------------------------------------------------------------
# stream 节：空闲保活配置（方案 19 实施项 1a）
# ---------------------------------------------------------------------------

def test_stream_idle_reset_seconds_default() -> None:
    """StreamConfig.idle_reset_seconds 内置默认值 5.0 秒。"""
    from config.settings import StreamConfig

    assert StreamConfig().idle_reset_seconds == 5.0


def test_base_yaml_stream_idle_reset() -> None:
    """base.yaml 的 stream 节同步声明 idle_reset_seconds（与默认值一致，防漂移）。"""
    section = load_yaml_config("base").get("stream", {})

    assert section["idle_reset_seconds"] == 5.0