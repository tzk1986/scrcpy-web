# tests/unit/test_cli_screenrecord.py
"""
AdbCliDriver 录屏 CLI 封装测试（方案 37 B2）
==============================================

monkeypatch AdbCliDriver._run_serial 记录命令序列、模拟 screenrecord
启动成败与 pkill/pidof 输出，覆盖：

    - start_recording：命令组装 / err=-38 降档重试 / 非 err=-38 不重试
    - stop_recording：pkill 主路径 / 无产物空串 / toybox pidof+kill -INT 兜底
    - pull_recording：成功路径 / 远程缺失 None / 其他错误重抛
    - rm_remote：命令组装 / 失败仅警告
"""

import asyncio
import os

import pytest

from app.core.exceptions import AdbError
from app.infrastructure.adb import cli as cli_module
from app.infrastructure.adb.cli import AdbCliDriver


async def test_start_recording_assembles_cmd(monkeypatch):
    calls = []
    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        return ""
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    await d.start_recording("192.168.8.18:5555", "/sdcard/r.mp4", "4M", 60)
    assert calls == [("192.168.8.18:5555", "shell", "screenrecord", "--bit-rate", "4M", "--time-limit", "60", "/sdcard/r.mp4")]


async def test_start_recording_timeout_is_time_limit_plus_30(monkeypatch):
    """M1 补强：_run_serial 必须显式收到 timeout=time_limit+30（_run 默认 30s 会杀掉 60s 限额）。"""
    calls = []
    async def fake_run(adr, *args, timeout=None):
        calls.append((args, timeout))
        return ""
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    await AdbCliDriver().start_recording("d", "/sdcard/r.mp4", "4M", 60)
    assert calls[0][1] == 60 + 30


async def test_err38_triggers_degraded_retry(monkeypatch):
    calls = []
    async def fake_run(adr, *args, timeout=None):
        calls.append((args, timeout))
        if "256x144" not in args:
            raise AdbError("Unable to get output buffers (err=-38)")
        return ""
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    await AdbCliDriver().start_recording("d", "/sdcard/r.mp4", "4M", 60)
    assert any("256x144" in c[0] for c in calls)
    # M1：降档重试同样显式收到 timeout=time_limit+30
    assert all(t == 60 + 30 for _, t in calls)


async def test_non_err38_fails_without_retry(monkeypatch):
    n = 0
    async def fake_run(adr, *args, timeout=None):
        nonlocal n
        n += 1
        raise AdbError("some other failure")
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    with pytest.raises(AdbError):
        await AdbCliDriver().start_recording("d", "/sdcard/r.mp4", "4M", 60)
    assert n == 1


async def test_err38_twice_raises_combined(monkeypatch):
    """err=-38 降档重试仍 err=-38：错误文本含首启与重试两份摘要，恰好两次调用。"""
    calls = []
    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        # 模拟 _run 抛出的真实形态：错误文本自带 "ADB command failed: " 前缀
        raise AdbError("ADB command failed: Unable to get output buffers (err=-38)")
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    with pytest.raises(AdbError) as ei:
        await AdbCliDriver().start_recording("d", "/sdcard/r.mp4", "4M", 60)
    assert len(calls) == 2
    assert str(ei.value).count("Unable to get output buffers (err=-38)") == 2
    assert "retry with 256x144 also failed" in str(ei.value)
    # M4：组合错误消息仅保留一个 "ADB command failed: " 前缀（首启/重试摘要去前缀合并）
    assert str(ei.value).count("ADB command failed: ") == 1
    assert str(ei.value) == (
        "ADB command failed: Unable to get output buffers (err=-38); "
        "retry with 256x144 also failed: Unable to get output buffers (err=-38)"
    )


async def test_err38_after_grace_window_still_degrades(monkeypatch):
    """B-Fix-2：err=-38 晚于启动宽限窗出现（.25 实测 ~3s > 2s 窗）仍触发降档重试。

    把宽限窗模块常量收缩到 5 轮 × 0.01s（~0.05s），fake 首启延迟 0.3s 后抛
    err=-38——start 返回时任务仍在跑（按「启动成功」存表），失败由任务完成
    回调捕获并降档。断言：降档 --size 256x144 第二次调用发出、恰好 2 次。
    """
    monkeypatch.setattr(cli_module, "_START_GRACE_ITERATIONS", 5)
    monkeypatch.setattr(cli_module, "_START_GRACE_INTERVAL", 0.01)
    calls = []

    async def fake_run(adr, *args, timeout=None):
        calls.append((args, timeout))
        if "--size" not in args:
            await asyncio.sleep(0.3)  # 超宽限窗的晚失败
            raise AdbError("ADB command failed: Unable to get output buffers (err=-38)")
        return ""  # 降档重试成功

    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    await d.start_recording("d", "/sdcard/r.mp4", "4M", 60)

    # start 已返回；等晚失败 + 回调降档链走完（表被重试条目替换后再自清）
    for _ in range(300):
        if any("--size" in args for args, _ in calls) and "d" not in d._recording_tasks:
            break
        await asyncio.sleep(0.01)
    assert len(calls) == 2, f"应恰好 2 次调用（首启+一次降档），实际 {len(calls)}：{calls}"
    degraded = [c for c in calls if "--size" in c[0]]
    assert degraded == [(
        ("d", "shell", "screenrecord", "--bit-rate", "4M",
         "--time-limit", "60", "--size", "256x144", "/sdcard/r.mp4"),
        60 + 30,
    )]


async def test_no_double_degrade(monkeypatch):
    """B-Fix-2 防重入：同一次 start 只降档一次——降档重试自身也晚失败时不再第三度拉起。"""
    monkeypatch.setattr(cli_module, "_START_GRACE_ITERATIONS", 5)
    monkeypatch.setattr(cli_module, "_START_GRACE_INTERVAL", 0.01)
    calls = []

    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        await asyncio.sleep(0.3)  # 首启与降档重试均晚失败
        raise AdbError("ADB command failed: Unable to get output buffers (err=-38)")

    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    await d.start_recording("d", "/sdcard/r.mp4", "4M", 60)

    # 等两次晚失败 + 二次回调（防重入路径：不再拉起第三条任务）收敛
    for _ in range(500):
        if len(calls) >= 2 and "d" not in d._recording_tasks:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # 缓冲一拍，防第三条调用迟到
    assert len(calls) == 2, f"应恰好 2 次调用（首启+一次降档、不三度拉起），实际 {len(calls)}：{calls}"
    assert [c for c in calls if "--size" in c] == calls[1:]


async def test_stop_sends_pkill_and_returns_remote_path(monkeypatch):
    calls = []
    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        return ""
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    remote = "/sdcard/r.mp4"
    await d.start_recording("d", remote, "4M", 60)
    assert await d.stop_recording("d") == remote
    assert ("d", "shell", "pkill", "-INT", "screenrecord") in calls


async def test_stop_no_product_returns_empty(monkeypatch):
    calls = []
    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        if "pkill" in args:
            # rc≠0 且 stderr 空 = 无匹配特征
            raise AdbError("ADB command failed: ")
        return ""
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    await d.start_recording("d", "/sdcard/r.mp4", "4M", 60)
    assert await d.stop_recording("d") == ""


async def test_stop_falls_back_to_kill_pid(monkeypatch):
    calls = []
    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        if "pkill" in args:
            raise AdbError("ADB command failed: pkill: not found")
        if "pidof" in args:
            return "1234"
        return ""
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    remote = "/sdcard/r.mp4"
    await d.start_recording("d", remote, "4M", 60)
    assert await d.stop_recording("d") == remote
    assert ("d", "shell", "kill", "-INT", "1234") in calls


async def test_pull_success_returns_local_path(monkeypatch):
    calls = []
    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        return ""
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    result = await AdbCliDriver().pull_recording("d", "/sdcard/r.mp4", "C:/tmp")
    assert calls == [("d", "pull", "/sdcard/r.mp4", "C:/tmp")]
    assert result == os.path.join("C:/tmp", os.path.basename("/sdcard/r.mp4"))


async def test_pull_remote_missing_returns_none(monkeypatch):
    async def fake_run(adr, *args, timeout=None):
        raise AdbError("ADB command failed: remote object '/sdcard/r.mp4' does not exist")
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    result = await AdbCliDriver().pull_recording("d", "/sdcard/r.mp4", "C:/tmp")
    assert result is None


async def test_pull_other_error_reraised(monkeypatch):
    async def fake_run(adr, *args, timeout=None):
        raise AdbError("boom")
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    with pytest.raises(AdbError):
        await AdbCliDriver().pull_recording("d", "/sdcard/r.mp4", "C:/tmp")


async def test_rm_remote_sends_rm(monkeypatch):
    calls = []
    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        return ""
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    await AdbCliDriver().rm_remote("d", "/sdcard/r.mp4")
    assert calls == [("d", "shell", "rm", "-f", "/sdcard/r.mp4")]


async def test_rm_remote_failure_only_warns(monkeypatch):
    async def fake_run(adr, *args, timeout=None):
        raise AdbError("boom")
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    await AdbCliDriver().rm_remote("d", "/sdcard/r.mp4")  # 不抛即通过