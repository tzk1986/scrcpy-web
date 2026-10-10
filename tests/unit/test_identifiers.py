"""标识符白名单单元测试（方案 37 A5）。"""

from app.domain.identifiers import is_valid_device_id, is_valid_package_name


def test_valid_device_ids():
    for v in ("192.168.8.18:5555", "FAKE-5555", "emulator-5554", "R58M12ABC", "dev_1"):
        assert is_valid_device_id(v), v


def test_invalid_device_ids():
    for v in ("x&calc", "a b", "a;b", "a|b", "", "a/b", 'a"b', "a%b"):
        assert not is_valid_device_id(v), v


def test_valid_package_names():
    for v in ("com.example", "com.example.app2", "a.b_c.d123"):
        assert is_valid_package_name(v), v


def test_invalid_package_names():
    for v in ("com.a;reboot", "com.a&&b", "com.a b", "", "com/a", "今.天"):
        assert not is_valid_package_name(v), v
