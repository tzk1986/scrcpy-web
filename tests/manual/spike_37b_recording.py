#!/usr/bin/env python3
"""方案 37 B8 spike：录屏链路真机验收（非 pytest，不入 CI）。

数据流：直接驱动应用层 RecordingService + 基础设施层 AdbCliDriver
（不必起 HTTP 服务），asyncio 编排；设备侧残留/环境观察用 subprocess
直连 adb。spike 只验收不改产品码。

用法（仓库根，Git Bash）：
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_37b_recording.py
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_37b_recording.py \
        --devices 192.168.8.18:5555 192.168.8.25:5555 --out /tmp/s37b.json

判据（结果逐项写入输出 JSON 的 verdicts 列表）：
    S37B-1 .18 正常录 5s → stop → pulled 文件存在于 data/recordings/
           → 设备端 /sdcard/openscrcpy_rec_*.mp4 已清（无残留）
    S37B-2 .18 单设备双 start → ConflictError（HTTP 层 409 语义）
    S37B-3 录屏目录双限 _enforce_limits：4 天前 mtime 旧文件删除 +
           超容量按 mtime 删旧（spike 局部把 max_total_mb 收敛到 1MB，
           dir 指到专用子目录——不改产品码、不碰真实产物）
    S37B-4 .25 首启 err=-38 → 自动降档 256x144 成功（跨任务约束②）
    S37B-5 .25 stop 信号路径观察：pkill 存在性 + 实际激活路径
           （pkill 主路径 vs pidof + kill -INT 兜底）
    S37B-6 周期清理任务 start/stop（lifespan 钩子路径，轻量观察）

退出码：存在 FAIL 判据 → 1；全 pass/观察 → 0。
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
# Git Bash 路径转换防御（子进程列参本不经 MSYS，双保险）
os.environ.setdefault("MSYS_NO_PATHCONV", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.application.recording_service import RecordingService  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.exceptions import AdbError, ConflictError  # noqa: E402
from app.domain.recording import RecordingState  # noqa: E402
from app.infrastructure.adb.cli import AdbCliDriver  # noqa: E402

DEFAULT_DEVICES = ["192.168.8.18:5555", "192.168.8.25:5555"]
REC_GLOB = "/sdcard/openscrcpy_rec_*.mp4"
DEGRADED_SIZE = "256x144"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def adb_shell(device: str, args: list[str], timeout: float = 30.0) -> tuple[int, str]:
    r = subprocess.run(
        ["adb", "-s", device, "shell", *args],
        capture_output=True, timeout=timeout,
    )
    out = r.stdout.decode("utf-8", errors="replace")
    err = r.stderr.decode("utf-8", errors="replace")
    log(f"adb[{device}] shell {' '.join(args)} -> rc={r.returncode} out={out[:120]!r} err={err[:120]!r}")
    return r.returncode, out


def list_remote_recordings(device: str) -> list[str]:
    """设备端 /sdcard 下 openscrcpy_rec_*.mp4 清单；无匹配 → []。"""
    rc, out = adb_shell(device, ["ls", "-1", REC_GLOB])
    return [ln.strip() for ln in out.splitlines() if ln.strip()] if rc == 0 else []


def clean_remote(device: str) -> None:
    adb_shell(device, ["rm", "-f", REC_GLOB])


def mp4_signature_ok(path: Path) -> bool:
    """轻量 MP4 校验：偏移 4 处为 ftyp box 名。"""
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] == b"ftyp"


def verdicts_summary(results: list[dict]) -> str:
    fails = [r for r in results if r["status"] == "fail"]
    return (
        f"{len(results)} 项：pass {sum(1 for r in results if r['status'] == 'pass')} / "
        f"observe {sum(1 for r in results if r['status'] == 'observe')} / "
        f"fail {len(fails)}"
    )


class SpyDriver:
    """把 _run_serial 包一层：记录每条设备命令的 args/成败/错误文本。

    spike 观察用（不注入产品行为）：据此判断降档重试与 stop 信号路径
    实际激活了哪条分支。
    """

    def __init__(self, driver: AdbCliDriver) -> None:
        self._driver = driver
        self.issued: list[dict] = []

    def install(self) -> None:
        original = self._driver._run_serial

        async def spy(device_id: str, *args: str, timeout: int | None = None) -> str:
            entry = {"device": device_id, "args": list(args), "ok": None, "err": ""}
            self.issued.append(entry)
            try:
                out = await original(device_id, *args, timeout=timeout)
                entry["ok"] = True
                return out
            except Exception as exc:  # noqa: BLE001 —— 观察层：记错后原样重抛
                entry["ok"] = False
                entry["err"] = str(exc)[:300]
                raise

        self._driver._run_serial = spy  # type: ignore[method-assign]

    def screenrecord_cmds(self, device: str) -> list[dict]:
        return [
            e for e in self.issued
            if e["device"] == device and e["args"][:1] == ["shell"]
            and "screenrecord" in e["args"]
        ]

    def after(self, index: int) -> list[dict]:
        """index 之后发出的命令（定位某次 stop 实际激活的收尾序列）。"""
        return self.issued[index + 1:]


async def drive_18(service: RecordingService, spy: SpyDriver, device: str,
                   spike_locals: list[Path]) -> list[dict]:
    """S37B-1 正常录 5s + S37B-2 双 start 互斥。"""
    results: list[dict] = []
    clean_remote(device)
    assert not list_remote_recordings(device), "预清后不应有残留"

    # ---- S37B-1 正常录 5s → stop → pull → 设备端即清 ----
    st = await service.start(device)
    log(f"S37B-1 start active session={st.id} state={st.state}")
    await asyncio.sleep(5)
    mark = len(spy.issued)
    done = await service.stop(device)
    assert done is not None and done.state == RecordingState.DONE.value
    if done.local_path:
        spike_locals.append(Path(done.local_path))

    remote = list_remote_recordings(device)
    local_ok = bool(done.local_path) and mp4_signature_ok(Path(done.local_path))
    size = Path(done.local_path).stat().st_size if done.local_path else 0
    no_degraded = all("--size" not in e["args"] for e in spy.screenrecord_cmds(device))
    results.append({
        "id": "S37B-1",
        "device": device,
        "status": "pass" if (local_ok and not remote) else "fail",
        "detail": (
            f"录 5s→stop→state=done；local={done.local_path}（{size}B，ftyp {local_ok}）；"
            f"设备端残留={remote or '无'}；全程无 --size（无降档）={no_degraded}"
        ),
        "stop_commands": [e["args"] for e in spy.after(mark) if e["device"] == device],
        "stop_ok_flags": [e["ok"] for e in spy.after(mark) if e["device"] == device],
    })

    # ---- S37B-2 单设备双 start → ConflictError ----
    st2 = await service.start(device)
    log(f"S37B-2 first start session={st2.id}")
    await asyncio.sleep(2)
    second = None
    try:
        await service.start(device)
        second = "no-error"
    except ConflictError:
        second = "conflict"
    results.append({
        "id": "S37B-2",
        "device": device,
        "status": "pass" if second == "conflict" else "fail",
        "detail": f"进行态会话第二次 start → {second}（HTTP 层 409 语义）",
    })
    done2 = await service.stop(device)
    if done2 and done2.local_path:
        spike_locals.append(Path(done2.local_path))
    remote2 = list_remote_recordings(device)
    results.append({
        "id": "S37B-2-cleanup",
        "device": device,
        "status": "pass" if (done2 and done2.state == RecordingState.DONE.value and not remote2) else "fail",
        "detail": f"互斥后正常 stop 收尾 state={done2.state if done2 else None}，设备端残留={remote2 or '无'}",
    })
    return results


async def drive_25(service: RecordingService, spy: SpyDriver, device: str,
                   spike_locals: list[Path]) -> list[dict]:
    """S37B-4 首启 err=-38 自动降档 + S37B-5 stop 信号路径观察。"""
    results: list[dict] = []
    clean_remote(device)
    assert not list_remote_recordings(device), "预清后不应有残留"

    # pkill 存在性（Android 7.1.2 toybox 环境实测）
    rc_wh, out_wh = adb_shell(device, ["command", "-v", "pkill"])
    rc_ls, out_ls = adb_shell(device, ["which", "pkill"])
    pkill_present = (
        (rc_wh == 0 and bool(out_wh.strip()))
        or (rc_ls == 0 and bool(out_ls.strip()))
    )
    log(f"pkill 存在性：command -v rc={rc_wh} out={out_wh!r}；which rc={rc_ls} out={out_ls!r}")

    before = len(spy.issued)
    t0 = time.monotonic()
    st = await service.start(device)
    start_elapsed = time.monotonic() - t0
    log(f"start 完成 elapsed={start_elapsed:.2f}s session={st.id}")

    rec_cmds = spy.screenrecord_cmds(device)
    first = rec_cmds[0] if rec_cmds else None
    degraded = [e for e in rec_cmds if "--size" in e["args"]]
    first_err38 = bool(first and first["ok"] is False and "err=-38" in first["err"])
    results.append({
        "id": "S37B-4",
        "device": device,
        "status": "pass" if (degraded and first_err38) else "fail",
        "detail": (
            f"screenrecord 调用 {len(rec_cmds)} 次；首启失败文本含 err=-38={first_err38}；"
            f"降档重试（--size {DEGRADED_SIZE}）已发出={bool(degraded)}；"
            f"start 总耗时 {start_elapsed:.2f}s"
        ),
        "first_error": (first or {}).get("err", ""),
    })

    await asyncio.sleep(5)
    mark = len(spy.issued)
    done = await service.stop(device)
    if done and done.local_path:
        spike_locals.append(Path(done.local_path))
    stop_cmds = [e for e in spy.after(mark) if e["device"] == device]
    pkill_entries = [e for e in stop_cmds if "pkill" in e["args"]]
    pidof_entries = [e for e in stop_cmds if "pidof" in e["args"]]
    kill_entries = [e for e in stop_cmds if "kill" in e["args"]]
    active_path = (
        "pidof+kill -INT 兜底" if kill_entries else
        ("pkill 主路径" if pkill_entries else "未知（无信号命令捕获）")
    )
    pkill_result = pkill_entries[0]["ok"] if pkill_entries else None
    remote = list_remote_recordings(device)
    local_ok = bool(done and done.local_path) and mp4_signature_ok(Path(done.local_path)) \
        if done and done.local_path else False
    results.append({
        "id": "S37B-5",
        "device": device,
        "status": "pass" if (done and done.state == RecordingState.DONE.value and local_ok and not remote) else "fail",
        "detail": (
            f"state={done.state if done else None}；local={getattr(done, 'local_path', '') or '无'}（ftyp {local_ok}）；"
            f"设备端残留={remote or '无'}；pkill 设备端存在={pkill_present}；"
            f"stop 实际激活路径={active_path}（pkill rc 语义 ok={pkill_result}，"
            f"pidof 关联命令 {len(pidof_entries)} 条 / kill 关联命令 {len(kill_entries)} 条）"
        ),
        "stop_commands": [e["args"] for e in stop_cmds],
    })
    return results


async def drive_dual_limit(service: RecordingService, device: str) -> list[dict]:
    """S37B-3 目录双限：人工造 4 天前旧文件 + 超容量 → _enforce_limits 清干净。

    spike 局部：max_total_mb 收敛到 1MB、dir 指到专用子目录（用完即清），
    不触碰真实录屏产物；_enforce_limits 本体零改动。
    """
    results: list[dict] = []
    cfg = settings().recording
    orig_dir, orig_mb = cfg.dir, cfg.max_total_mb
    spike_dir = Path(service.resolve_dir()) / f".spike37b_lim_{time.strftime('%H%M%S')}"
    spike_dir.mkdir(parents=True, exist_ok=True)
    try:
        cfg.dir = str(spike_dir)
        cfg.max_total_mb = 1
        now = time.time()
        old = time.time() - 4 * 86400
        # 2 个 4 天前旧文件（时间限命中）+ 4 个近期各 400KB（容量限命中）
        for i in range(2):
            (spike_dir / f"old_{i}.mp4").write_bytes(b"x" * 300_000)
            os.utime(spike_dir / f"old_{i}.mp4", (old, old))
        for i in range(4):
            (spike_dir / f"rec_{i}.mp4").write_bytes(b"x" * 400_000)
            os.utime(spike_dir / f"rec_{i}.mp4", (now, now))
        before = sorted(p.name for p in spike_dir.iterdir())
        RecordingService._enforce_limits(spike_dir)
    finally:
        cfg.dir, cfg.max_total_mb = orig_dir, orig_mb

    after = sorted(p.name for p in spike_dir.iterdir())
    removed = sorted(set(before) - set(after))
    total_after = sum(p.stat().st_size for p in spike_dir.iterdir()) if spike_dir.is_dir() else 0
    old_gone = not any(n.startswith("old_") for n in after)
    cap_ok = total_after <= 1 * 1024 * 1024
    results.append({
        "id": "S37B-3",
        "device": device,
        "status": "pass" if (old_gone and cap_ok and "old_0.mp4" in removed) else "fail",
        "detail": (
            f"双限目录清理：移除 {removed}；残留 {after}（总 {total_after}B）；"
            f"时间限清旧={old_gone}，容量限 ≤1MB={cap_ok}"
        ),
    })
    # 用完即清（数据保留策略）
    import shutil
    shutil.rmtree(spike_dir, ignore_errors=True)
    log(f"S37B-3 spike 目录已清：{spike_dir}")
    return results


async def drive_cleanup_task(service: RecordingService) -> list[dict]:
    """S37B-6 周期清理任务 start/stop（lifespan 启动路径同款调用）。"""
    results: list[dict] = []
    await service.start_cleanup_task()
    running = bool(service.cleanup_task and not service.cleanup_task.done())
    await service.stop_cleanup_task()
    stopped = bool(service.cleanup_task is None or service.cleanup_task.done())
    results.append({
        "id": "S37B-6",
        "device": "-",
        "status": "pass" if (running and stopped) else "fail",
        "detail": f"start_cleanup_task 后任务运行={running}；stop 后已停/空={stopped}",
    })
    return results


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", nargs="*", default=DEFAULT_DEVICES)
    parser.add_argument("--out", default=None, help="结果 JSON 输出路径（可选）")
    args = parser.parse_args()

    rec_cfg = settings().recording
    driver = AdbCliDriver()
    spy = SpyDriver(driver)
    spy.install()
    service = RecordingService(adb=driver)

    log(f"有效录屏配置：dir={rec_cfg.dir} bit_rate={rec_cfg.bit_rate} "
        f"time_limit={rec_cfg.time_limit} retention_days={rec_cfg.retention_days} "
        f"max_total_mb={rec_cfg.max_total_mb} degraded_size={rec_cfg.degraded_size}")
    log(f"录制落盘目录 resolve_dir={service.resolve_dir()}")

    # 设备在线预检
    online = (await driver._run("devices")).strip()  # noqa: SLF001 —— spike 观察
    log(f"adb devices 首段：{online.splitlines()[:4]}")

    results: list[dict] = []
    spike_locals: list[Path] = []
    for device in args.devices:
        if device == "192.168.8.18:5555":
            results += await drive_18(service, spy, device, spike_locals)
        elif device == "192.168.8.25:5555":
            results += await drive_25(service, spy, device, spike_locals)
        else:
            log(f"跳过未知用例设备 {device}（本 spike 仅编排 .18/.25）")

    results += await drive_dual_limit(service, "-")
    results += await drive_cleanup_task(service)

    log("==== spike 结果汇总 ====")
    for r in results:
        print(f"  [{r['status'].upper():7s}] {r['id']:<14s} {r['detail']}", flush=True)
    print(verdicts_summary(results), flush=True)

    if args.out:
        payload = {
            "scheme": "37-B",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "devices": args.devices,
            "verdicts": results,
        }
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"JSON 已写 {args.out}")

    # spike 产物用完即清（数据保留策略）：只删本 spike 落盘文件，不动既有产物
    for path in spike_locals:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if spike_locals:
        log(f"spike 落盘产物已清：{len(spike_locals)} 个（{', '.join(p.name for p in spike_locals)}）")

    return 1 if any(r["status"] == "fail" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))