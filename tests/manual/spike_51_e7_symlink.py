"""方案 51 E7 真机实测：设备端 readlink 复核对软链逃逸的拦截。

用法（Git Bash）：
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python tests/manual/spike_51_e7_symlink.py <device_id>

场景（全部结束后设备侧清理）：
  1) 逃逸链接删除：/data/local/tmp/t51_link -> /system/app → 期望 PermissionDenied
  2) 白名单内链接删除：t51_inner -> /data/local/tmp/t51_plain.txt → 期望放行且只删链接
  3) 正常文件删除 → 期望放行
"""

import asyncio
import sys

from app.application.file_service import FileService
from app.core.exceptions import PermissionDeniedError
from app.infrastructure.adb.cli import AdbCliDriver


async def device_shell(adb: AdbCliDriver, dev: str, cmd: str) -> str:
    return await adb.shell(dev, cmd)


async def main(dev: str) -> None:
    adb = AdbCliDriver()
    svc = FileService(adb=adb)
    link = "/data/local/tmp/t51_link"
    inner = "/data/local/tmp/t51_inner"
    plain = "/data/local/tmp/t51_plain.txt"

    await device_shell(adb, dev, f"rm -f {link} {inner}; echo hello > {plain}")
    await device_shell(adb, dev, f"ln -s /system/app {link}")
    await device_shell(adb, dev, f"ln -s {plain} {inner}")
    ls_out = await device_shell(adb, dev, f"ls -l {link} {inner} {plain}")
    print("[setup]\n" + ls_out)

    # 1) 逃逸链接 → 期望 403
    try:
        results = await svc.delete(dev, [link])
        print(f"[case1] FAIL 未拦截: {results}")
    except PermissionDeniedError as e:
        print(f"[case1] PASS 拦截 403: {e}")

    # 链接目标文件未被误删？
    check = await device_shell(adb, dev, f"ls /system/app >/dev/null 2>&1 && echo target-alive")
    print(f"[case1-echo] {check.strip()}")

    # 2) 白名单内链接 → 期望放行，只删链接（目标 plain 仍在）
    results = await svc.delete(dev, [inner])
    print(f"[case2] results={results}")
    still = await device_shell(adb, dev, f"test -e {plain} && echo plain-alive")
    print(f"[case2-echo] {still.strip()}")

    # 3) 正常文件 → 期望放行
    results = await svc.delete(dev, [plain])
    print(f"[case3] results={results}")

    await device_shell(adb, dev, f"rm -f {link} {inner} {plain}")
    print("[cleanup] done")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "192.168.8.25:5555"))
