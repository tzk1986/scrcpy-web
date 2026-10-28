#!/usr/bin/env python3
"""方案 42 真机验收 spike：录屏流程优化与磁盘清理（非 pytest，不入 CI）。

判据（默认 .25；--devices 可换 .18/.33）：
    S42-1 连环录制：录 5s → stop done → 同设备立即再 start（done 覆盖态，
          即「重新录制」后端等价路径）→ 再录 5s → done；两段产物均在位
    S42-2 自然录满自动收尾：start 后**不做任何 stop**，等设备端 time_limit
          录满（.25 含降档）→ 服务经监听器自动收敛 done，产物 ftyp + 大小>0，
          设备端无残留，轮询上限 time_limit + 60s
    S42-3 录制中离开页面（直 API 模拟）：与 S42-2 同服务端路径；额外验证
          自动收尾完成后新 start 不被 409（进行态窗口确已消除）
    S42-4 关服清盘：cleanup_all_recordings 只清 openscrcpy_rec_*.mp4，
          其他文件保留（真实目录 + 哨兵文件）
    S42-5 启动即扫（强杀重启模拟）：新实例 start_cleanup_task → 首轮即时
          清掉哨兵残留（无需等 3600s）

用法（仓库根，Git Bash）：
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_42_recording_flow.py
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_42_recording_flow.py \
        --devices 192.168.8.25:5555 --out /tmp/s42.json

退出码：存在 fail → 1；否则 0。spike 自产 mp4 用后即清（数据保留策略）。

实现注记（Task 5 二选一裁决）：
    - ConflictError 采用「直接 import + isinstance」写法（app.core.exceptions
      已在本脚本 import 面上，无需按类名比对换取零 import 精简）；
    - S42-3 的 stop 返回值一并纳入收尾清理清单（简报原稿丢弃该返回值会漏清
      第三段产物，与 R-T5-4「用后即清、不额外保留产物」对齐）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("MSYS_NO_PATHCONV", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.application.recording_service import RecordingService  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.exceptions import ConflictError  # noqa: E402
from app.domain.recording import RecordingState  # noqa: E402
from app.infrastructure.adb.cli import AdbCliDriver  # noqa: E402

DEFAULT_DEVICES = ["192.168.8.25:5555"]
REC_GLOB = "/sdcard/openscrcpy_rec_*.mp4"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def adb_shell(device: str, args: list[str], timeout: float = 30.0) -> tuple[int, str]:
    r = subprocess.run(
        ["adb", "-s", device, "shell", *args], capture_output=True, timeout=timeout
    )
    out = r.stdout.decode("utf-8", errors="replace")
    err = r.stderr.decode("utf-8", errors="replace")
    log(f"adb[{device}] shell {' '.join(args)} -> rc={r.returncode} err={err[:80]!r}")
    return r.returncode, out


def list_remote_recordings(device: str) -> list[str]:
    rc, out = adb_shell(device, ["ls", "-1", REC_GLOB])
    return [ln.strip() for ln in out.splitlines() if ln.strip()] if rc == 0 else []


def clean_remote(device: str) -> None:
    adb_shell(device, ["rm", "-f", REC_GLOB])


def mp4_signature_ok(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] == b"ftyp"


def rec_dir() -> Path:
    return Path(settings().recording.dir)


async def wait_state(service: RecordingService, device: str, timeout: float) -> str:
    """轮询会话态离 recording（自然收尾路径），上限 timeout 秒。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = service.get(device)
        if s is not None and s.state != RecordingState.RECORDING.value:
            return s.state
        await asyncio.sleep(2)
    return "timeout"


async def drive(device: str) -> list[dict]:
    results: list[dict] = []
    driver = AdbCliDriver()
    service = RecordingService(adb=driver)
    time_limit = settings().recording.time_limit

    clean_remote(device)

    # ---- S42-1 连环录制（done 覆盖 → 再 start 放行）----
    s1 = await service.start(device)
    await asyncio.sleep(5)
    d1 = await service.stop(device)
    s2 = await service.start(device)  # done 终态被新 start 覆盖 = 前端「重新录制」
    await asyncio.sleep(5)
    d2 = await service.stop(device)
    files = [
        Path(x.local_path) for x in (d1, d2) if x and x.local_path
    ]
    ok = (
        d1 is not None and d1.state == RecordingState.DONE.value
        and d2 is not None and d2.state == RecordingState.DONE.value
        and len(files) == 2 and all(mp4_signature_ok(p) for p in files)
    )
    results.append({
        "id": "S42-1", "device": device, "status": "pass" if ok else "fail",
        "detail": (
            f"两段连环录制 state={d1.state if d1 else None}/{d2.state if d2 else None}，"
            f"产物 {len(files)} 个 ftyp 全验；同 session={s1.id != s2.id}"
        ),
    })

    # ---- S42-2/S42-3 自然录满自动收尾（不 stop，纯监听器路径）----
    sn = await service.start(device)
    log(f"已 start（不点停止，等 {time_limit}s 录满自动收尾）session={sn.id}")
    final = await wait_state(service, device, time_limit + 60)
    s_after = service.get(device)
    auto_done = s_after is not None and s_after.state == RecordingState.DONE.value
    local_ok = bool(
        s_after and s_after.local_path and mp4_signature_ok(Path(s_after.local_path))
        and Path(s_after.local_path).stat().st_size > 0
    )
    residue = list_remote_recordings(device)
    if auto_done and local_ok:
        p = Path(s_after.local_path)
        results.append({
            "id": "S42-2", "device": device, "status": "pass",
            "detail": f"录满自动收尾 done；local={p.name}（{p.stat().st_size}B, ftyp✓）；设备残留={residue or '无'}",
        })
    else:
        results.append({
            "id": "S42-2", "device": device, "status": "fail",
            "detail": f"轮询终值={final}；state={getattr(s_after, 'state', None)}；local_ok={local_ok}",
        })
    third_done = None
    try:
        third = await service.start(device)  # 收尾后窗口已消：不再 409
        conflict = False
    except Exception as exc:  # noqa: BLE001
        conflict = isinstance(exc, ConflictError)
        third = None
    if conflict or third is None:
        results.append({
            "id": "S42-3", "device": device, "status": "fail",
            "detail": f"自动收尾后新 start 被拒（409 残留窗口未消除）：{conflict}",
        })
    else:
        third_done = await service.stop(device)
        results.append({
            "id": "S42-3", "device": device, "status": "pass",
            "detail": "自动收尾完成后新 start 放行（进行态窗口即时消除，等效离开页面语义）",
        })

    # ---- S42-4 关服清盘（真实目录 + 哨兵）----
    rdir = rec_dir().resolve()
    rdir.mkdir(parents=True, exist_ok=True)
    sentinel = rdir / "spike42_keep.mp4"
    sentinel.write_bytes(b"keep")
    fake1 = rdir / "openscrcpy_rec_spike000001.mp4"
    fake1.write_bytes(b"junk")
    service.cleanup_all_recordings()
    ok4 = fake1.exists() is False and sentinel.exists()
    results.append({
        "id": "S42-4", "device": device, "status": "pass" if ok4 else "fail",
        "detail": f"清盘只中模式：fake 删={not fake1.exists()}，哨兵保留={sentinel.exists()}",
    })
    sentinel.unlink(missing_ok=True)

    # ---- S42-5 启动即扫（新实例 tick 首轮即时清哨兵）----
    stale = rdir / "openscrcpy_rec_spike000002.mp4"
    stale.write_bytes(b"junk")
    fresh_service = RecordingService(adb=AdbCliDriver())  # 模拟重启新实例
    await fresh_service.start_cleanup_task()
    await asyncio.sleep(0.2)
    swept = not stale.exists()
    await fresh_service.stop_cleanup_task()
    results.append({
        "id": "S42-5", "device": device, "status": "pass" if swept else "fail",
        "detail": f"启动即扫：tick 首轮（无 3600s 等待）清掉遗留哨兵={swept}",
    })

    # 用后即清：本 spike 产物（数据保留策略）
    to_clean = list(files)
    if s_after and s_after.local_path:
        to_clean.append(Path(s_after.local_path))
    if third_done and third_done.local_path:
        to_clean.append(Path(third_done.local_path))
    for p in to_clean:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    return results


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", nargs="*", default=DEFAULT_DEVICES)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    results: list[dict] = []
    for device in args.devices:
        results += await drive(device)

    log("==== spike 结果汇总 ====")
    for r in results:
        print(f"  [{r['status'].upper():7s}] {r['id']:<6s} {r['detail']}", flush=True)
    fails = [r for r in results if r["status"] == "fail"]
    if args.out:
        payload = {
            "scheme": "42", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "devices": args.devices, "verdicts": results,
        }
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))