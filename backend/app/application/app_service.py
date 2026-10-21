"""
应用管理服务
=============

层：应用层。

提供 Android 设备应用管理功能：
- 列出已安装应用（区分系统/第三方）
- 查看应用详情（版本、大小、内存占用）
- 启动/停止应用
- 卸载应用
- 清除应用数据

参考方案文档：方案/14-调试面板其他标签完善.md
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.core.exceptions import AdbError, InvalidIdentifierError
from app.core.logging import get_logger
from app.domain.identifiers import is_valid_package_name
from app.domain.ports import AdbDriver

logger = get_logger(__name__)

# dumpsys 中的标志位块：`flags=[ ... ]` / `pkgFlags=[ ... ]` / `privateFlags=[ ... ]`
_FLAGS_BLOCK_RE = re.compile(r"[Ff]lags=\[([^\]]*)\]")


def _has_system_flag(text: str) -> bool:
    """
    判断 dumpsys 输出中是否含系统应用标志。

    真机（Android 11+）输出形如 `pkgFlags=[ SYSTEM HAS_CODE ALLOW_BACKUP ]`，
    按空白分词后要求存在独立的 "SYSTEM" 标志：既兼容旧式 `flags=[ SYSTEM ]`，
    也不会把权限行的 `flags=[ SYSTEM_FIXED|... ]`（竖线分隔的复合标志）误判为系统应用。
    """
    return any("SYSTEM" in match.group(1).split() for match in _FLAGS_BLOCK_RE.finditer(text))


def _extract_timestamp(text: str, field: str) -> str:
    """
    提取 firstInstallTime/lastUpdateTime 时间戳。

    真机格式含时分秒且中间有空格（如 `2025-11-02 14:30:22`），需捕获整行剩余内容；
    字段缺失时返回空串。
    """
    match = re.search(rf"{field}=([^\r\n]+)", text)
    return match.group(1).strip() if match else ""


# `dumpsys activity processes` 的进程行：ProcessRecord{<hex> <pid>:<package>/<user>}
# hex 字符类 [0-9a-fA-F] 已覆盖大小写（方案 51 C15：双份解析合一；
# 原两处「使用 re.IGNORECASE」失实注释——实际未传 flag——随之删除）。
_PROCESS_RECORD_RE = re.compile(r"ProcessRecord\{[0-9a-fA-F]+ (\d+):([^/}]+)")


def _iter_process_records(output: str) -> Iterable[tuple[int, str]]:
    """按文本顺序产出 ProcessRecord 的 (pid, package)。"""
    for match in _PROCESS_RECORD_RE.finditer(output):
        yield int(match.group(1)), match.group(2).strip()


@dataclass
class AppInfo:
    """应用信息。"""

    package_name: str
    version_name: str
    version_code: int
    install_time: str
    update_time: str
    apk_size_mb: float
    is_system: bool
    is_running: bool
    pid: int | None
    memory_kb: int | None


class AppService:
    """
    应用管理服务。

    通过 ADB 命令管理设备上的应用。

    使用示例：
        service = AppService(adb_driver)
        apps = await service.list_apps("192.168.1.100")
        await service.launch_app("192.168.1.100", "com.example.app")
    """

    def __init__(self, adb: AdbDriver):
        """
        初始化服务。

        参数：
            adb: ADB 驱动实例。
        """
        self.adb = adb

    async def list_apps(
        self,
        device_id: str,
        include_system: bool = False,
    ) -> list[AppInfo]:
        """
        列出设备上的应用。

        优化策略（批量查询，避免逐应用 ADB 调用）：
        1. `pm list packages -f [-s|-3]` 获取包名和 APK 路径
        2. 单次 `dumpsys package` 批量解析所有包的版本/安装时间/系统标志
        3. 单次 `dumpsys activity processes` 获取运行中进程
        4. 跳过逐个 `ls -l` 获取 APK 大小（列表页不需要）

        参数：
            device_id: 设备 ID。
            include_system: 是否包含系统应用（默认仅第三方应用）。

        返回：
            AppInfo 列表。
        """
        try:
            # Step 1: 获取包列表
            flags = "-f"
            if include_system:
                cmd = f"pm list packages {flags}"
            else:
                cmd = f"pm list packages {flags} -3"

            output = await self.adb.shell(device_id, cmd)

            packages: list[tuple[str, str]] = []  # (package_name, apk_path)
            for line in output.splitlines():
                line = line.strip()
                if not line.startswith("package:"):
                    continue
                # 包名是最后一个 "=" 之后的部分：Android 11+ 的 APK 路径本身含 "=="
                # （如 /data/app/~~XxYy==/pkg-AbCd==/base.apk），包名不含 "="，
                # 故用贪婪匹配让 (.+) 回溯到最后一个 "=" 处切分；兼容老式无 "=" 的路径。
                match = re.match(r"package:(.+)=([^=]+)$", line)
                if match:
                    packages.append((match.group(2), match.group(1)))

            if not packages:
                return []

            # Step 2: 批量获取版本/安装时间/系统标志（单次 dumpsys package）
            dumpsys_output = await self.adb.shell(device_id, "dumpsys package")
            package_info_map = self._parse_dumpsys_packages(dumpsys_output)

            # Step 3: 批量获取运行中进程（单次 dumpsys activity processes）
            running_map = await self._get_running_processes(device_id)

            # Step 4: 组装结果
            apps: list[AppInfo] = []
            for package_name, apk_path in packages:
                info = package_info_map.get(package_name, {})
                pid = running_map.get(package_name)

                apps.append(AppInfo(
                    package_name=package_name,
                    version_name=info.get("version_name", ""),
                    version_code=info.get("version_code", 0),
                    install_time=info.get("install_time", ""),
                    update_time=info.get("update_time", ""),
                    apk_size_mb=0.0,  # 列表页不显示大小（需逐个 ls -l，太慢）
                    is_system=info.get("is_system", False),
                    is_running=pid is not None,
                    pid=pid,
                    memory_kb=None,  # 列表页不查内存（需逐个 dumpsys meminfo，太慢）
                ))

            logger.info("apps_listed", device=device_id, count=len(apps), include_system=include_system)
            return apps

        except Exception as e:
            # 方案 51 C9：不吞 ADB 异常——上抛交 HTTP 映射明确报错
            # （离线设备从假空列表变可见错误；未预期异常同样不再伪装成空）
            logger.error("list_apps_failed", device=device_id, error=str(e))
            raise

    def _parse_dumpsys_packages(self, output: str) -> dict[str, dict[str, Any]]:
        """
        从 `dumpsys package` 输出中批量解析所有包的信息。

        返回：
            {package_name: {version_name, version_code, install_time, update_time, is_system}}
        """
        result: dict[str, dict[str, Any]] = {}

        # 按 "Package [" 分割，每个块是一个包的信息
        # 格式示例：
        #   Package [com.example.app (uid: 10001, flags: ...):
        #     versionCode=123 firstInstallTime=2024-01-01 lastUpdateTime=2024-06-01
        #     versionName=1.2.3
        blocks = re.split(r"Package \[([^\]]+)\]", output)

        # blocks[0] 是第一个 Package 之前的内容
        # blocks[1] 是第一个包名, blocks[2] 是第一个包的内容
        # blocks[3] 是第二个包名, blocks[4] 是第二个包的内容 ...
        for i in range(1, len(blocks) - 1, 2):
            pkg_name = blocks[i]
            block = blocks[i + 1]

            # 只取到下一个 Package 定义之前
            next_pkg = block.find("\n  Package [")
            if next_pkg >= 0:
                block = block[:next_pkg]

            info: dict[str, Any] = {}

            # 版本号
            vname = re.search(r"versionName=(\S+)", block)
            if vname:
                info["version_name"] = vname.group(1)
            else:
                info["version_name"] = ""

            vcode = re.search(r"versionCode=(\d+)", block)
            info["version_code"] = int(vcode.group(1)) if vcode else 0

            # 安装时间（真机含时分秒，如 2025-11-02 14:30:22）
            info["install_time"] = _extract_timestamp(block, "firstInstallTime")
            info["update_time"] = _extract_timestamp(block, "lastUpdateTime")

            # 系统应用标志（pkgFlags=[ SYSTEM ... ] 按空白分词判断）
            info["is_system"] = _has_system_flag(block)

            result[pkg_name] = info

        return result

    async def _get_running_processes(self, device_id: str) -> dict[str, int]:
        """
        获取运行中的进程列表（单次 ADB 调用）。

        解析 `dumpsys activity processes` 输出。
        格式：`*APP* UID <uid> ProcessRecord{<hex> <pid>:<package>/<user>}`

        返回：
            {package_name: pid}
        """
        try:
            output = await self.adb.shell(device_id, "dumpsys activity processes")
            result: dict[str, int] = {}
            for pid, package in _iter_process_records(output):
                if package and package not in result:
                    result[package] = pid

            return result

        except Exception as e:
            logger.warning("get_running_processes_failed", device=device_id, error=str(e))
            return {}

    async def get_app_info(self, device_id: str, package: str) -> AppInfo | None:
        """
        获取应用详情。

        参数：
            device_id: 设备 ID。
            package: 包名。

        返回：
            AppInfo 对象，或 None（应用不存在）。
        """
        if not is_valid_package_name(package):
            raise InvalidIdentifierError("package")
        return await self._get_app_info_internal(device_id, package)

    async def _get_app_info_internal(
        self,
        device_id: str,
        package: str,
        apk_path: str | None = None,
    ) -> AppInfo | None:
        """内部方法：获取应用详情；包不存在时返回 None。"""
        try:
            # 获取 APK 路径（如果未提供）
            if not apk_path:
                apk_path = await self._get_apk_path(device_id, package)

            # 获取 dumpsys package 信息
            output = await self.adb.shell(device_id, f"dumpsys package {package}")

            # 包不存在时真机输出 "Unable to find package: xxx"（无 "Package [包名]" 块），
            # 按调用方契约（HTTP 404 / 前端兜底）返回 None，不再返回空字段 AppInfo
            if not re.search(rf"Package \[{re.escape(package)}\]", output):
                logger.info("get_app_info_package_not_found", device=device_id, package=package)
                return None

            # 解析版本号
            version_name = ""
            version_code = 0
            version_match = re.search(r"versionName=(\S+)", output)
            if version_match:
                version_name = version_match.group(1)
            code_match = re.search(r"versionCode=(\d+)", output)
            if code_match:
                version_code = int(code_match.group(1))

            # 解析安装时间（真机含时分秒，如 2025-11-02 14:30:22）
            install_time = _extract_timestamp(output, "firstInstallTime")
            update_time = _extract_timestamp(output, "lastUpdateTime")

            # 获取 APK 大小
            apk_size_mb = 0.0
            if apk_path:
                try:
                    size_output = await self.adb.shell(device_id, f"ls -l {apk_path}")
                    # ls -l 格式：-rw-r--r-- 1 system system 68456117 2026-08-04 19:16 /path
                    # 文件大小是第 4 个字段（在 owner 和 group 之后）
                    size_match = re.search(r"\S+\s+\d+\s+\S+\s+\S+\s+(\d+)", size_output)
                    if size_match:
                        apk_size_mb = int(size_match.group(1)) / (1024 * 1024)
                except Exception as e:
                    logger.warning("get_apk_size_failed", package=package, error=str(e))

            # 判断是否系统应用（真机为 pkgFlags=[ SYSTEM HAS_CODE ... ]，
            # 按空白分词判断，与 _parse_dumpsys_packages 保持同一逻辑）
            is_system = _has_system_flag(output)

            # 检查是否运行中（使用 dumpsys activity processes）
            is_running, pid = await self._check_app_running(device_id, package)

            # 获取内存占用
            memory_kb = None
            if is_running:
                memory_kb = await self._get_app_memory(device_id, package)

            return AppInfo(
                package_name=package,
                version_name=version_name,
                version_code=version_code,
                install_time=install_time,
                update_time=update_time,
                apk_size_mb=round(apk_size_mb, 2),
                is_system=is_system,
                is_running=is_running,
                pid=pid,
                memory_kb=memory_kb,
            )

        except Exception as e:
            # 方案 51 C9：不吞异常——ADB 故障上抛（原吞 None 致假 404）；
            # 「包不存在」由 Package 块检查显式返回 None，二者语义分离
            logger.warning("get_app_info_failed", device=device_id, package=package, error=str(e))
            raise

    async def _get_apk_path(self, device_id: str, package: str) -> str | None:
        """获取应用的 APK 路径。"""
        try:
            output = await self.adb.shell(device_id, f"pm list packages -f {package}")
            # 输出格式：package:/data/app/xxx/base.apk=com.example.app
            match = re.search(rf"package:(.+?)={re.escape(package)}", output)
            if match:
                return match.group(1)
            return None
        except Exception:
            return None

    async def _check_app_running(self, device_id: str, package: str) -> tuple[bool, int | None]:
        """检查应用是否运行中，返回 (is_running, pid)。"""
        try:
            # 直接获取完整输出，不用 grep（某些设备不支持）
            output = await self.adb.shell(device_id, "dumpsys activity processes")

            # 方案 51 C15：复用与 _get_running_processes 相同的单份解析
            for pid, pkg in _iter_process_records(output):
                if pkg == package:
                    return True, pid

            return False, None

        except Exception as e:
            logger.warning("check_app_running_failed", device=device_id, package=package, error=str(e))
            return False, None

    async def _get_app_memory(self, device_id: str, package: str) -> int | None:
        """获取应用内存占用（KB）。"""
        try:
            output = await self.adb.shell(device_id, f"dumpsys meminfo {package}")

            # 尝试多种格式匹配
            # 格式 1: TOTAL: 6968       (Android 常见格式)
            # 格式 2: TOTAL PSS: 12345 kB
            # 格式 3: Total PSS: 12345 kB
            patterns = [
                r"^\s*TOTAL:\s+(\d+)",  # TOTAL: 6968
                r"TOTAL\s+PSS[:\s]+(\d+)",
                r"Total\s+PSS[:\s]+(\d+)",
            ]

            for pattern in patterns:
                match = re.search(pattern, output, re.MULTILINE)
                if match:
                    return int(match.group(1))

            return None

        except Exception as e:
            logger.warning("get_app_memory_failed", device=device_id, package=package, error=str(e))
            return None

    async def launch_app(self, device_id: str, package: str) -> bool:
        """
        启动应用。

        参数：
            device_id: 设备 ID。
            package: 包名。

        返回：
            是否成功。
        """
        if not is_valid_package_name(package):
            raise InvalidIdentifierError("package")
        try:
            # 使用 monkey 启动应用，--pct-syskeys 0 跳过物理按键检查
            cmd = f"monkey -p {package} -c android.intent.category.LAUNCHER --pct-syskeys 0 1"
            output = await self.adb.shell(device_id, cmd)

            success = "Events injected: 1" in output
            if success:
                logger.info("app_launched", device=device_id, package=package)
            else:
                logger.warning("app_launch_failed", device=device_id, package=package, output=output)

            return success

        except Exception as e:
            logger.error("launch_app_error", device=device_id, package=package, error=str(e))
            return False

    async def stop_app(self, device_id: str, package: str) -> bool:
        """
        强制停止应用。

        参数：
            device_id: 设备 ID。
            package: 包名。

        返回：
            是否成功。
        """
        if not is_valid_package_name(package):
            raise InvalidIdentifierError("package")
        try:
            await self.adb.shell(device_id, f"am force-stop {package}")
            logger.info("app_stopped", device=device_id, package=package)
            return True

        except Exception as e:
            logger.error("stop_app_error", device=device_id, package=package, error=str(e))
            return False

    async def uninstall_app(self, device_id: str, package: str) -> bool:
        """
        卸载应用。

        参数：
            device_id: 设备 ID。
            package: 包名。

        返回：
            是否成功。
        """
        if not is_valid_package_name(package):
            raise InvalidIdentifierError("package")
        try:
            output = await self.adb.shell(device_id, f"pm uninstall {package}")
            success = "Success" in output

            if success:
                logger.info("app_uninstalled", device=device_id, package=package)
            else:
                logger.warning("app_uninstall_failed", device=device_id, package=package, output=output)

            return success

        except Exception as e:
            logger.error("uninstall_app_error", device=device_id, package=package, error=str(e))
            return False

    async def clear_app_data(self, device_id: str, package: str) -> bool:
        """
        清除应用数据。

        参数：
            device_id: 设备 ID。
            package: 包名。

        返回：
            是否成功。
        """
        if not is_valid_package_name(package):
            raise InvalidIdentifierError("package")
        try:
            output = await self.adb.shell(device_id, f"pm clear {package}")
            success = "Success" in output

            if success:
                logger.info("app_data_cleared", device=device_id, package=package)
            else:
                logger.warning("app_clear_failed", device=device_id, package=package, output=output)

            return success

        except Exception as e:
            logger.error("clear_app_data_error", device=device_id, package=package, error=str(e))
            return False

    async def get_app_memory(self, device_id: str, package: str) -> int | None:
        """
        获取应用内存占用。

        参数：
            device_id: 设备 ID。
            package: 包名。

        返回：
            内存占用（KB），或 None。
        """
        if not is_valid_package_name(package):
            raise InvalidIdentifierError("package")
        return await self._get_app_memory(device_id, package)

    async def get_runtime_info(self, device_id: str, package: str) -> dict[str, object]:
        """
        查询应用运行时状态（方案 37 C3）。

        把 ADB 层 RuntimeInfo 收敛为前端可直用的 dict：
        - top_activity / resumed 为空串视为解析项缺失，各追加一条 parse_warning；
        - services / providers 为空清单属合法空，不计 warning；
        - AdbError 不抛出，收敛为空形态 dict（parse_warning 提示整体失败，截 200 字符内）。

        参数：
            device_id: 设备 ID。
            package: 包名（端点路径参数，直接作为返回 dict 的 pkg 键）。

        返回：
            {pkg, top_activity, resumed, services, providers, parse_warning}
        """
        if not is_valid_package_name(package):
            raise InvalidIdentifierError("package")
        try:
            info = await self.adb.get_runtime_info(device_id)
        except AdbError as e:
            logger.warning(
                "get_app_runtime_failed", device=device_id, package=package, error=str(e)
            )
            message = f"ADB 查询失败: {e}"
            return {
                "pkg": package,
                "top_activity": "",
                "resumed": "",
                "services": [],
                "providers": [],
                "parse_warning": message[:200],
            }

        warnings: list[str] = []
        if not info.resumed:
            warnings.append("resumed 缺失")
        if not info.top_activity:
            warnings.append("top_activity 缺失")
        return {
            "pkg": package,
            "top_activity": info.top_activity,
            "resumed": info.resumed,
            "services": info.services,
            "providers": info.providers,
            "parse_warning": "; ".join(warnings),
        }
