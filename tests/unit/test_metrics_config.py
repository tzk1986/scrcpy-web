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
    }