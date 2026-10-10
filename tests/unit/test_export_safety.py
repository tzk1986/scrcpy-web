# tests/unit/test_export_safety.py
from app.interfaces.http.export_safety import sanitize_export_filename

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
