"""方案 58 A-03 真机复核（.20 RK3568 A7 慢存储）：连发起停 20 轮查设备端孤儿。

判据：每轮 start→首帧→stop 后，设备端 grep /proc/*/cmdline 应无 scrcpy.Server
残留（修复前若枚举超时/早败可留孤儿，下次 start 被 stray 检测终生阻断）。
运行：PYTHONPATH=backend python tests/manual/spike_58a03_orphan_20.py
"""
import asyncio
import sys
from pathlib import Path

# app.core.config 依赖仓库根的 config.settings，双路径自举免受启动方式影响
_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT / "backend"), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.core.config import settings
from app.domain.ports import EncoderOpts
from app.infrastructure.stream.scrcpy import ScrcpyEncoder

DEV = "192.168.8.20:5555"
# [.] 防 grep/sh 自身命令行字面自匹配（与后端 _STRAY_GREP_CMD 同式）
GREP = "grep -lE 'scrcpy[.]Server' /proc/[0-9]*/cmdline 2>/dev/null"
ROUNDS = 20


async def orphans() -> list[str]:
    proc = await asyncio.create_subprocess_exec(
        settings().adb.path, "-s", DEV, "shell", GREP,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=getattr(asyncio, "CREATE_NO_WINDOW", 0),
    )
    out, _ = await proc.communicate()
    return [ln.strip() for ln in out.decode(errors="replace").splitlines() if ln.strip()]


async def one_round(i: int) -> str:
    enc = ScrcpyEncoder()
    note = "ok"
    gen = enc.start(DEV, EncoderOpts())
    try:
        await asyncio.wait_for(anext(gen), timeout=10)
    except StopAsyncIteration:
        note = "stream ended before first frame"
    except TimeoutError:
        note = "no first frame in 10s"
    except Exception as e:  # 早败/占用等，均为复核信息
        note = f"start error: {type(e).__name__}: {e}"
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass
        try:
            await enc.stop()
        except Exception as e:
            note += f" | stop error: {e}"
    await asyncio.sleep(0.5)  # 给设备端进程退场一点时间
    orph = await orphans()
    return f"{note} | orphans={len(orph)} {orph}"


async def main() -> int:
    pre = await orphans()
    print(f"pre-existing orphans: {pre}")
    bad = 0
    for i in range(1, ROUNDS + 1):
        line = await one_round(i)
        if "orphans=0" not in line:
            bad += 1
        print(f"round {i:2d}: {line}", flush=True)
    verdict = "CLEAN 20/20" if bad == 0 else f"ORPHAN in {bad}/{ROUNDS} rounds"
    print(f"REVIEW RESULT: {verdict}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
