# tests/unit/test_export_safety.py
from app.interfaces.http.export_safety import csv_safe, sanitize_export_filename


def test_csv_safe_escapes_formula_leading_chars():
    """方案 58 C-02/C-03：设备可控串首字符 = + - @ 前插 ' 中和电子表格公式。"""
    for evil in ("=1+1", "+1+1", "-1+1", "@SUM(A1)"):
        assert csv_safe(evil) == "'" + evil


def test_csv_safe_leaves_plain_text_untouched():
    assert csv_safe("MyWiFi") == "MyWiFi"
    assert csv_safe("") == ""
    assert csv_safe("com.example/.MainActivity") == "com.example/.MainActivity"
    assert csv_safe("-50") == "'-50"  # 负数串同样中和（导出为文本语义）


def test_csv_safe_passes_non_string():
    assert csv_safe(123) == 123
    assert csv_safe(1.5) == 1.5
    assert csv_safe(None) is None
    assert csv_safe(True) is True

def test_path_traversal_neutralized():
    assert sanitize_export_filename("../etc/passwd") == "_.._etc_passwd" or ".." not in sanitize_export_filename("../etc/passwd").replace("_", "")

def test_absolute_windows_path_neutralized():
    out = sanitize_export_filename(r"C:\Windows\system32\x.json")
    assert ":" not in out and "\\" not in out

def test_unicode_control_and_spaces():
    out = sanitize_export_filename('a b"c;rm*.json')
    assert all(ch.isalnum() or ch in "._-" for ch in out)

def test_no_leading_dot():
    assert not sanitize_export_filename("..hidden").startswith(".")

def test_empty_falls_back():
    assert sanitize_export_filename("///") == "export"

def test_valid_name_untouched():
    assert sanitize_export_filename("perf_192.168.8.18_123.csv") == "perf_192.168.8.18_123.csv"
