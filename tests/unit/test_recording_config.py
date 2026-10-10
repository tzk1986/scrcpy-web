# tests/unit/test_recording_config.py
def test_recording_config_defaults():
    from app.core.config import settings
    r = settings().recording
    assert r.bit_rate == "4M" and r.time_limit == 60
    assert r.retention_days == 3 and r.max_total_mb == 512
    assert r.degraded_size == "256x144"

def test_default_recordings_dir_under_data():
    from app.core.config import settings
    assert settings().recording.dir.startswith("./data/")
