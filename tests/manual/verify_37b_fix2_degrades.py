#!/usr/bin/env python3
"""方案 37 B-Fix-2 真机复验：.25 降档竞态修复（非 pytest，不入 CI）。

驱动基础设施层 AdbCliDriver 直验（不起 HTTP）：start_recording 默认
4M/60s 参数 → 首启 err=-38 晚失败（~3s，超 2s 宽限窗）→ 完成回调降档
（--size 256x144）→ stop 收敛（pkill + await 收尾 + pull 产物 + 设备端即清）。

用法（仓库根，Git Bash）：
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/verify_37b_fix2_degrades.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("MSYS_NO_PATHCONV", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.infrastructure.adb.cli import AdbCliDriver  # noqa: E402

DEVICE = "192.168.8.25:5555"
DEGRADED_SIZE = "256x144"
REMOTE = "/sdcard/openscrcpy_fix2_verify.mp4"
WAIT_DEGRADE_S = 15  # 降档触发等待上界（.25 晚失败实测 ~3s，留足余量）


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Spy:
    """记录 screenrecord 相关命令（不成败语义观察用，不注入产品行为）。"""

    def __init__(self, driver: AdbCliDriver) -> None:
        self._driver = driver
        self.cmds: list[tuple[str, ...]] = []

    def install(self) -> None:
        original = self._driver._run_serial

        async def spy(device_id: str, *args: str, timeout: int | None = None) -> str:
            # 实例属性赋值不触发绑定：调用时首个实参即 device_id（同 spike SpyDriver）
            self.cmds.append(args)
            return await original(device_id, *args, timeout=timeout)

        self._driver._run_serial = spy  # type: ignore[method-assign]

    def screenrecords(self) -> list[tuple[str, ...]]:
        return [c for c in self.cmds if "screenrecord" in c]


async def main() -> int:
    driver = AdbCliDriver()
    spy = Spy(driver)
    spy.install()

    online = await driver._run("devices")  # noqa: SLF001
    log(f"adb devices 首段：{online.splitlines()[:4]}")
    if DEVICE not in online:
        log(f"FAIL：{DEVICE} 不在 adb devices 列表")
        return 1

    await driver.rm_remote(DEVICE, REMOTE)

    t0 = time.monotonic()
    await driver.start_recording(DEVICE, REMOTE, "4M", 60)
    log(f"start_recording 返回，耗时 {time.monotonic() - t0:.2f}s（宽限窗后按成功返回）")

    # 等晚失败（~3s）+ 回调降档：spy 看到含 --size 的第二次 screenrecord 调用
    degraded = None
    for _ in range(WAIT_DEGRADE_S * 20):
        recs = spy.screenrecords()
        degraded = [c for c in recs if "--size" in c]
        if len(recs) >= 2 and degraded:
            break
        await asyncio.sleep(0.05)

    recs = spy.screenrecords()
    first = recs[0] if recs else None
    log(f"screenrecord 调用共 {len(recs)} 次：{recs}")
    first_cmd = (
        "shell", "screenrecord", "--bit-rate", "4M", "--time-limit", "60", REMOTE
    )
    deg_ok = bool(degraded) and degraded[0] == (
        "shell", "screenrecord", "--bit-rate", "4M", "--time-limit", "60",
        "--size", DEGRADED_SIZE, REMOTE,
    )
    log(f"降档判定 degraded_ok={deg_ok}（首启={first == first_cmd}，降档={degraded}）")
    if not deg_ok:
        log("FAIL：降档重试未按预期出现")
        return 1

    # 降档后让重试录 3s（256x144 低负载，spike 基线 2s/8KB）再 stop 收敛
    await asyncio.sleep(3)
    t1 = time.monotonic()
    remote = await driver.stop_recording(DEVICE)
    log(f"stop_recording 返回 remote={remote!r}，耗时 {time.monotonic() - t1:.2f}s")

    if not remote:
        log("FAIL：stop 未返回产物路径（空表即返，降档条目已丢失？）")
        return 1

    local_dir = ROOT / "data" / "recordings" / ".fix2_verify"
    local_dir.mkdir(parents=True, exist_ok=True)
    local = await driver.pull_recording(DEVICE, remote, str(local_dir))
    ftyp_ok = bool(local)
    size = 0
    if local:
        head = Path(local).read_bytes()[:12]
        ftyp_ok = len(head) >= 8 and head[4:8] == b"ftyp"
        size = Path(local).stat().st_size
        Path(local).unlink(missing_ok=True)
    await driver.rm_remote(DEVICE, REMOTE)
    local_dir.rmdir()

    log(f"产物校验：local={local}（{size}B，ftyp {ftyp_ok}）；设备端已清；spike 暂存目录已清")
    if not ftyp_ok:
        log("FAIL：产物非有效 MP4")
        return 1

    log("PASS：.25 降档竞态修复真机复验通过（晚失败降档 + stop 收敛 + 产物有效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))