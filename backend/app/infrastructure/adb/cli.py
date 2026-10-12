"""
ADB CLI 驱动 — 基于子进程的 ADB 实现
========================================

层：基础设施 → ADB。

通过 asyncio.create_subprocess_exec 调用 `adb` 命令行工具
实现 AdbDriver 协议（domain/ports.py）。

这是最简单、最可移植的实现——适用于任何 ADB 版本，
不需要额外的 Python 库。权衡是每个命令有子进程开销，
对于当前用例（设备管理，非高频 I/O）是可以接受的。

关键实现细节：
    - 所有命令通过 _run() 或 _run_serial()，处理
      stdout/stderr 捕获和超时
    - stream_logcat() 使用长运行子进程逐行读取
    - screenshot() 使用主机上的临时文件中转 PNG 数据
    - ADB 二进制路径从配置读取（settings().adb.path）

安全考虑：
    - 用户输入的 shell 命令直接传递给 `adb shell`。
      在生产环境中，应添加输入验证/清理。
    - ADB 路径来自配置（非用户输入），所以是可信的。
"""

import asyncio
import os
import re
from typing import AsyncIterator, Callable, cast

from app.core.config import settings
from app.core.exceptions import AdbError, DeviceUnreachableError
from app.core.logging import get_logger
from app.core.platform import CREATE_NO_WINDOW
from app.domain.device import DeviceInfo
from app.domain.ports import ShellSession
from app.domain.runtime import (
    BatteryInfo,
    CpuProcessInfo,
    RuntimeInfo,
    ServiceInfo,
    TopActivityInfo,
)
from app.infrastructure.adb.reachability import probe_tcp, scan_open_hosts

logger = get_logger(__name__)

# 截图数据流魔数（方案 38 A2）：多屏设备 screencap 的 stderr 警告经
# exec-out 合并进 stdout 首位，剥前缀时以此为锚点。
PNG_MAGIC = b"\x89PNG"
GZIP_MAGIC = b"\x1f\x8b"


# _run 失败错误文本的统一前缀（start_recording 组合双摘要时按此前缀去重）
_ADB_FAILED_PREFIX = "ADB command failed: "

# screenrecord 启动宽限窗（方案 37 B-Fix-2 提为模块常量，测试可 monkeypatch
# 收缩窗口以在单测时限内复现「晚失败超窗」竞态）：默认 20 轮 × 0.1s = 2s。
_START_GRACE_ITERATIONS = 20
_START_GRACE_INTERVAL = 0.1


def _strip_lead_junk(data: bytes, magic: bytes) -> bytes:
    """从魔数处剥离输出流首位噪声；魔数缺失或在 0 位则原样返回（只增不减）。"""
    pos = data.find(magic)
    return data[pos:] if pos > 0 else data


def _strip_adb_prefix(text: str) -> str:
    """剥掉 _run 错误文本自带的 "ADB command failed: " 前缀（组合摘要防双前缀）。"""
    return text[len(_ADB_FAILED_PREFIX):] if text.startswith(_ADB_FAILED_PREFIX) else text


# dumpsys activity 系运行时查询的超时（秒，方案 37 C2）：输出体量
# 100KB-500KB（三机连采样本 87KB-435KB），_INFO_CMD_TIMEOUT 的 5s 对慢设备偏紧，
# 故单独放宽到 10s；仍远低于诊断类命令的 adb.timeout（30s）。
_RUNTIME_CMD_TIMEOUT = 10

# --- 方案 37 C2：dumpsys activity 输出解析纯函数（模块层，供 get_runtime_info
# 直接调用与单测喂 fixture；逐行正则锚定，不依赖行号/字段顺序，未知格式行
# 天然跳过——漂移样本（tests/application/fixtures/README.md）锁定「不抛」）---

# 行锚定（re.M 级）：先整行捕获，再由组件正则从行内容中提取 "pkg/class"。
_RESUMED_ACTIVITY_RE = re.compile(r"^ *mResumedActivity: (.+)$", re.M)
# top 备位链：7.1.2 形态 mFocusedActivity，Android 11 形态 mFocusedApp=（两正则皆试）。
_FOCUSED_ACTIVITY_RE = re.compile(r"^ *mFocusedActivity: (.+)$", re.M)
_FOCUSED_APP_RE = re.compile(r"^ *mFocusedApp=(.+)$", re.M)
_COMPONENT_RE = re.compile(r"([\w.]+)/(\.?[\w.]+)")

# services：锚定 ServiceRecord{ 行首轮廓（* 前缀，主输出内嵌块与追加段同构），
# 名称取记录头尾段；重复行照单全收（双来源并存时不合并）。
_SERVICE_RECORD_RE = re.compile(r"^\s*\*\s*ServiceRecord\{\S+\s+u\d+\s+(.+)\}$")
# 状态来源：记录 stanza 体内的 4 空格 app= 续行（null=stopped，ProcessRecord=running），
# 扫描到非缩进续行即止（不跨 stanza）。
_SERVICE_APP_RE = re.compile(r"^    app=(.+)$")
_PROVIDER_RECORD_RE = re.compile(r"^\s*\*\s*ContentProviderRecord\{\S+\s+u\d+\s+(.+)\}$")


def _extract_component(record_content: str) -> str:
    """从 ActivityRecord{...} 行内容提取 "pkg/class" 组件串；形态不符返回空串。"""
    m = _COMPONENT_RE.search(record_content)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def parse_activity_runtime(text: str) -> RuntimeInfo:
    """
    解析 dumpsys activity 主输出的顶层状态（方案 37 C2）。

    resumed ← mResumedActivity 行；top_activity ← mFocusedActivity /
    mFocusedApp= 备位链（形态版本差异，两者皆试）。任一锚定行缺失 → 空串。

    参数：
        text: dumpsys activity 完整输出。

    返回：
        只填充 pkg/top_activity/resumed 的 RuntimeInfo（services/providers
        由专用命令的 parse_services/parse_providers 组装）。
    """
    resumed = ""
    m = _RESUMED_ACTIVITY_RE.search(text)
    if m:
        resumed = _extract_component(m.group(1))

    top_activity = ""
    m = _FOCUSED_ACTIVITY_RE.search(text)
    if not m:
        m = _FOCUSED_APP_RE.search(text)
    if m:
        top_activity = _extract_component(m.group(1))

    snapshot = TopActivityInfo(
        pkg=resumed.split("/", 1)[0] if resumed else "",
        top_activity=top_activity,
        resumed=resumed,
    )
    return RuntimeInfo(
        pkg=snapshot.pkg,
        top_activity=snapshot.top_activity,
        resumed=snapshot.resumed,
        services=[],
        providers=[],
    )


def parse_services(text: str) -> list[ServiceInfo]:
    """
    解析 dumpsys activity services 输出的 Service 清单（方案 37 C2）。

    锚定 ServiceRecord{ 行首轮廓提取「pkg/class」组件名；状态取 stanza 体内
    紧随的 4 空格 app= 续行（null=stopped，ProcessRecord=running），扫到
    非缩进续行即止（app= 缺失 → unknown，不跨 stanza 取邻行）。
    双来源并存时重复行照单全收。

    参数：
        text: dumpsys activity services 完整输出（混入主输出内嵌块亦兼容）。

    返回：
        ServiceInfo 清单；无记录时为空列表。
    """
    services: list[ServiceInfo] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _SERVICE_RECORD_RE.match(line)
        if not m:
            continue
        state = "unknown"
        for follow in lines[i + 1:]:
            if not follow.startswith("    "):
                break
            am = _SERVICE_APP_RE.match(follow)
            if am:
                state = "stopped" if am.group(1) == "null" else "running"
                break
        services.append(ServiceInfo(name=m.group(1), state=state))
    return services


def parse_providers(text: str) -> list[str]:
    """
    解析 dumpsys activity providers 输出的 Provider 清单（方案 37 C2）。

    锚定 ContentProviderRecord{ 行首轮廓提取「pkg/class」组件串；
    无记录/格式漂移 → 空列表（不抛）。

    参数：
        text: dumpsys activity providers 完整输出。

    返回：
        Provider 组件串清单；无记录时为空列表。
    """
    providers: list[str] = []
    for line in text.splitlines():
        m = _PROVIDER_RECORD_RE.match(line)
        if m:
            providers.append(m.group(1))
    return providers


# --- 方案 37 D2：dumpsys cpuinfo / dumpsys battery 输出解析纯函数（模块层，
# 供 get_cpu_usage / get_battery_info 直接调用与单测喂 fixture；逐行正则锚定，
# 未知格式行天然跳过——漂移样本锁定「不抛」）---

# 进程行正则（真实样本三变体：带 faults 后缀、无 faults 后缀、`+` 前缀且无
# faults；进程名内可含冒号——kworker/3:0、kworker/u9:1、kworker/0:1H-kblockd，
# (.+?):\s+ 非贪婪定位名尾「冒号+空白」，/ faults: 后缀不参与）。TOTAL 汇总行
# （`1.9% TOTAL: ...`）因 pid 段非数字自然不匹配。
_PROC_CPU_RE = re.compile(
    r"^\s*\+?\s*(\d+(?:\.\d+)?)%\s+(\d+)/(.+?):\s+"
    r"(\d+(?:\.\d+)?)% user \+ (\d+(?:\.\d+)?)% kernel"
)

# battery 字段行正则：行首字段名锚定（drift 样本中 `Max charging voltage:` 仍含
# voltage 一词，裸子串断言会误命中）；re.M 逐行搜索。
_BATTERY_LEVEL_RE = re.compile(r"^ *level:\s*(\d+)", re.M)
_BATTERY_TEMP_RE = re.compile(r"^ *temperature:\s*(\d+)", re.M)
_BATTERY_VOLTAGE_RE = re.compile(r"^ *voltage:\s*(\d+)", re.M)
_BATTERY_HEALTH_RE = re.compile(r"^ *health:\s*(\d+)", re.M)

# BatteryManager.BATTERY_HEALTH_* 码 → 枚举名（未识别码 → UNKNOWN）。
_HEALTH_NAME_BY_CODE: dict[int, str] = {
    1: "UNKNOWN",
    2: "GOOD",
    3: "OVERHEAT",
    4: "DEAD",
    5: "OVER_VOLTAGE",
    6: "UNSPECIFIED_FAILURE",
    7: "COLD",
}


def parse_cpuinfo(text: str) -> list[CpuProcessInfo]:
    """
    解析 dumpsys cpuinfo 输出的进程级 CPU 明细（方案 37 D2）。

    先行定位 `CPU usage from` 头行，其后逐进程行以行首正则锚定
    「N% pid/名: X% user + Y% kernel」：名段取 pid/ 后至名尾（不含 pid），
    非贪婪「冒号+空白」定位名尾，`/ faults:` 后缀不参与；`+` 前缀新进程
    形态按普通进程解析。TOTAL 汇总行形态不符自然跳过。头行缺失或进程段
    为空 → 空列表（不抛）。

    参数：
        text: dumpsys cpuinfo 完整输出。

    返回：
        CpuProcessInfo 清单；无进程时为空列表。
    """
    marker = text.find("CPU usage from")
    if marker == -1:
        return []
    procs: list[CpuProcessInfo] = []
    for line in text[marker:].splitlines():
        m = _PROC_CPU_RE.match(line)
        if m:
            procs.append(
                CpuProcessInfo(
                    proc=m.group(3),
                    user=float(m.group(4)),
                    kernel=float(m.group(5)),
                )
            )
    return procs


def parse_battery(text: str) -> BatteryInfo:
    """
    解析 dumpsys battery 输出四字段（方案 37 D2）。

    行首字段名锚定（`^ *key:`，re.M）；temperature 原值为十分之一摄氏度，
    解析层 ÷10 后为摄氏浮点（26 → 2.6、424 → 42.4，板级怪值如实不特判）；
    health 码按 BATTERY_HEALTH_* 枚举映射为串（未识别码 → UNKNOWN）。
    行缺失/不可解析 → None（level → 0），不抛。

    参数：
        text: dumpsys battery 完整输出。

    返回：
        BatteryInfo（level/temperature/voltage/health）。
    """
    level = 0
    m = _BATTERY_LEVEL_RE.search(text)
    if m:
        level = int(m.group(1))

    temperature: float | None = None
    m = _BATTERY_TEMP_RE.search(text)
    if m:
        temperature = int(m.group(1)) / 10.0

    voltage: int | None = None
    m = _BATTERY_VOLTAGE_RE.search(text)
    if m:
        voltage = int(m.group(1))

    health: str | None = None
    m = _BATTERY_HEALTH_RE.search(text)
    if m:
        health = _HEALTH_NAME_BY_CODE.get(int(m.group(1)), "UNKNOWN")

    return BatteryInfo(
        level=level,
        temperature=temperature,
        voltage=voltage,
        health=health,
    )


class AdbCliDriver:
    """
    基于子进程 CLI 调用的 ADB 驱动。

    实现 domain.ports.AdbDriver 协议。
    """

    # 采集启动命令：-T 以「设备端当前时刻」为下界，只收此刻起的日志，
    # 不回放设备端现存缓冲（方案 17 宣称「开启时才从当前时刻起收」，
    # 修复暂停→开始/重启采集时旧缓冲整段重放并以新 seq 重复入库）。
    # 下界时间由设备端 date 在命令执行瞬间生成（%N 纳秒），避免主机与
    # 设备时钟差导致重放或空窗；-T '<time>' 形式与 'MM-DD hh:mm:ss.mmm...'
    # 精度已真机验证（Android 11；另测 `-T 1` 计数形式会回放 1 行，
    # 纯数字参数按计数解析，不可用于零重放）。经 shell 以单参数传入
    #（同 shell_stream 约定，避免远端 shell 把带空格的时刻拆成两个参数）。
    _LOGCAT_SINCE_NOW_CMD = 'logcat -v threadtime -T "$(date "+%m-%d %H:%M:%S.%N")"'

    # 设备信息快命令（getprop / wm size / dumpsys battery）的超时（秒，
    # 方案 35 D2）：正常设备单条实测 0.15-0.2s，5s 对慢设备仍有余量，
    # 卡设备从「30s 阻塞」降为「5s + 信息缺失（列表兜底）」；诊断类
    # 命令（install/screenshot 等）继续用配置的 adb.timeout（30s）。
    _INFO_CMD_TIMEOUT = 5

    def __init__(self) -> None:
        # 录屏会话表（方案 37 B2）：后台 screenrecord 任务与对应设备端产物路径。
        # stop 时用 task 收尾、用 paths 返回 remote_path；任务完成回调自清两项，
        # 防 "Task exception was never retrieved" 与自然结束后的孤儿表项。
        self._recording_tasks: dict[str, asyncio.Task[str]] = {}
        self._recording_paths: dict[str, str] = {}
        # 降档重入抑制（方案 37 B-Fix-2）：同 device 同一次 start 只降档一次。
        # start 入口 discard 重置，新一次启动重获降档资格。
        self._degraded_once: set[str] = set()

    @property
    def adb_path(self) -> str:
        """ADB 可执行文件路径（运行时读取配置，热重载即时生效）。"""
        return settings().adb.path

    async def _run(self, *args: str, timeout: int | None = None) -> str:
        """
        执行 ADB 命令并返回 stdout。

        参数：
            *args: 命令参数（如 "devices"、"-s"、"serial"、"shell"、"ls"）。
            timeout: 覆盖配置中的默认超时（秒）。

        返回：
            去除首尾空白的 stdout 字符串。

        异常：
            AdbError: 命令以非零返回码退出时。
            AdbError: ADB 未安装时。
            AdbError: 命令超过超时时。
        """
        timeout = timeout or settings().adb.timeout
        cmd = [self.adb_path, *args]
        logger.debug("adb_command", cmd=cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            logger.error("adb_not_found", path=self.adb_path)
            raise AdbError(
                f"ADB not found at '{self.adb_path}'. "
                "Please install Android SDK Platform Tools and ensure it's in PATH."
            )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            logger.error("adb_command_timeout", cmd=cmd, timeout=timeout)
            raise AdbError(f"ADB command timed out after {timeout}s: {' '.join(cmd)}")

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error("adb_command_failed", cmd=cmd, returncode=proc.returncode, error=error_msg)
            raise AdbError(f"{_ADB_FAILED_PREFIX}{error_msg}")

        return stdout.decode().strip()

    async def _run_serial(
        self, device_id: str, *args: str, timeout: int | None = None
    ) -> str:
        """
        执行针对特定设备的 ADB 命令。

        在命令前添加 "-s {device_id}" 以选择目标设备。

        参数：
            device_id: ADB 序列号。
            *args: 设备选择器之后的命令参数。
            timeout: 覆盖配置中的默认超时（秒）。

        返回：
            去除首尾空白的 stdout 字符串。
        """
        return await self._run("-s", device_id, *args, timeout=timeout)

    async def list_devices(self) -> list[str]:
        """
        列出已连接设备的序列号。

        解析 `adb devices` 的输出，格式如：
            List of devices attached
            emulator-5554    device
            192.168.1.5:5555 device

        返回：
            序列号列表（只包含有 "device" 的行）。
        """
        output = await self._run("devices")
        lines = output.split("\n")[1:]  # 跳过标题行
        return [line.split()[0] for line in lines if "device" in line]

    async def get_device_info(self, device_id: str) -> DeviceInfo:
        """
        通过 ADB shell 查询设备属性并构建 DeviceInfo。

        4 条命令相互独立，并发执行（方案 35 D2）——单台设备总耗时上界
        从串行的 4×5s 压到 max(单条) ≤ _INFO_CMD_TIMEOUT。
        return_exceptions=True 必不可少：首异常取消其余命令会跳过 _run
        的超时 kill 收尾路径，留下孤儿 adb 进程。

        参数：
            device_id: ADB 序列号。

        返回：
            填充了设备属性的 DeviceInfo。

        异常：
            AdbError: 任一命令失败或超时时（沿用「整台失败」语义）。
        """
        results = await asyncio.gather(
            self._run_serial(
                device_id, "shell", "getprop", "ro.product.model",
                timeout=self._INFO_CMD_TIMEOUT,
            ),
            self._run_serial(
                device_id, "shell", "getprop", "ro.build.version.release",
                timeout=self._INFO_CMD_TIMEOUT,
            ),
            self._run_serial(
                device_id, "shell", "wm", "size",
                timeout=self._INFO_CMD_TIMEOUT,
            ),
            self._get_battery_level(device_id, timeout=self._INFO_CMD_TIMEOUT),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

        # gather(return_exceptions=True) 返回 list[object]；异常已在上方
        # 排除，4 个元素的类型由各命令签名保证
        model = cast(str, results[0])
        os_version = cast(str, results[1])
        wm_size = cast(str, results[2])
        battery = cast(int, results[3])

        return DeviceInfo(
            id=device_id,
            model=model,
            os_version=os_version,
            resolution=self._parse_resolution(wm_size),
            battery=battery,
            status="online",
        )

    def _parse_resolution(self, output: str) -> tuple[int, int]:
        """
        从 `wm size` 输出解析屏幕分辨率。

        预期输入格式：
            "Physical size: 1080x1920"
        或有覆盖时：
            "Physical size: 1080x1920\\nOverride size: 720x1280"

        参数：
            output: `adb shell wm size` 的原始输出。

        返回：
            (宽, 高) 元组。解析失败时返回 (0, 0)。
        """
        for line in output.split("\n"):
            if "Physical size" in line:
                size = line.split(":")[1].strip()
                w, h = size.split("x")
                return int(w), int(h)
        return 0, 0

    async def _get_battery_level(self, device_id: str, timeout: int | None = None) -> int:
        """
        从 `dumpsys battery` 获取电量。

        解析输出中的 "level: N" 行。

        参数：
            device_id: ADB 序列号。
            timeout: 覆盖配置中的默认超时（秒）。

        返回：
            电量 0-100。解析失败时返回 0。
        """
        output = await self._run_serial(device_id, "shell", "dumpsys", "battery", timeout=timeout)
        for line in output.split("\n"):
            if "level" in line:
                return int(line.split(":")[1].strip())
        return 0

    async def get_rotation(self, device_id: str) -> int:
        """
        查询设备显示 rotation（方案 40 B1）。

        使用 `dumpsys display` 解析默认显示器（首个）的
        `mCurrentOrientation=<n>` 行，数值为 Surface rotation 常量 0-3。
        方案 38 的 `dumpsys input SurfaceOrientation` 是 InputFlinger
        坐标系语义而非 Display rotation，已在 .19/.13 实测证伪
        （.13 且出现双行噪声）。

        参数：
            device_id: ADB 序列号。

        返回：
            rotation 值 0-3；行缺失或不可解析时返回 0（不旋转）。

        异常：
            AdbError: 命令执行失败（由应用层收敛为 0 并缓存）。
        """
        output = await self._run_serial(
            device_id, "shell", "dumpsys", "display", timeout=self._INFO_CMD_TIMEOUT
        )
        for line in output.split("\n"):
            if line.strip().startswith("mCurrentOrientation="):
                try:
                    return int(line.split("=")[1].strip())
                except ValueError:
                    return 0
        return 0

    async def shell(self, device_id: str, cmd: str) -> str:
        """
        在设备上执行 shell 命令。

        参数：
            device_id: ADB 序列号。
            cmd: 要执行的 shell 命令。

        返回：
            命令 stdout 字符串。
        """
        return await self._run_serial(device_id, "shell", cmd)

    async def shell_stream(self, device_id: str, cmd: str) -> AsyncIterator[str]:
        """
        流式执行 shell 命令，逐行产出输出（Pipe 模式，用于 HTTP API 降级）。

        修复：命令追加 `2>&1` 合并 stderr 到 stdout，同时启动后台任务消费 stderr。

        参数：
            device_id: ADB 序列号。
            cmd: 要执行的 shell 命令。

        产出：
            每次迭代产出一行输出（UTF-8 字符串）。
        """
        # 关键：追加 2>&1 合并 stderr
        proc = await asyncio.create_subprocess_exec(
            self.adb_path,
            "-s",
            device_id,
            "shell",
            f"{cmd} 2>&1",  # 合并 stderr 到 stdout
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
        assert proc.stdout is not None and proc.stderr is not None
        # 备用：启动后台任务消费 stderr（防止极端情况）
        stderr_task = asyncio.create_task(self._consume_stderr(proc.stderr))

        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                yield text.rstrip("\r\n") + "\n"

            # 读取 stderr（如果有遗漏）
            stderr_data = await proc.stderr.read()
            if stderr_data:
                yield stderr_data.decode("utf-8", errors="replace")
        finally:
            stderr_task.cancel()
            if proc.returncode is None:
                proc.kill()

    async def check_connection(self, device_id: str) -> bool:
        """
        检查设备是否仍然连接。

        通过执行一个简单的 shell 命令来检测设备是否在线。

        参数：
            device_id: ADB 序列号。

        返回：
            True 表示设备在线，False 表示设备离线。
        """
        try:
            await self._run_serial(device_id, "shell", "echo", "ping")
            return True
        except AdbError:
            logger.debug("device_not_connected", device=device_id)
            return False

    async def connect_tcp(self, ip: str, port: int = 5555, timeout: int | None = None) -> str:
        """
        通过 TCP/IP 连接到设备。

        用于无线调试或 USB 连接不稳定时的备用方案。

        参数：
            ip: 设备的 IP 地址。
            port: ADB 端口（默认 5555）。
            timeout: 覆盖 adb connect 命令超时（秒）；None 用配置默认（30s）。
                扫描编排路径传短超时（方案 36 D4），避免非 adb 服务拖满默认值。

        返回：
            设备 ID（格式为 "ip:port"）。

        异常：
            DeviceUnreachableError: TCP 预检不可达时（不进入 adb 子进程）。
            AdbError: 连接失败时。
        """
        device_id = f"{ip}:{port}"
        logger.info("connecting_tcp", ip=ip, port=port)

        # 预检：不可达直接快速失败，避免 adb connect 等待 SYN 重传 + 30s 命令超时
        await probe_tcp(ip, port, settings().adb.probe_timeout)

        try:
            output = await self._run("connect", device_id, timeout=timeout)
            if "connected" in output.lower():
                logger.info("tcp_connected", device=device_id)
                return device_id
            else:
                raise AdbError(f"Failed to connect to {device_id}: {output}")
        except AdbError as e:
            logger.error("tcp_connect_failed", ip=ip, port=port, error=str(e))
            raise

    async def disconnect_tcp(self, ip: str, port: int = 5555) -> None:
        """
        断开 TCP/IP 连接。

        参数：
            ip: 设备的 IP 地址。
            port: ADB 端口（默认 5555）。

        目标不可达时跳过 adb 调用直接返回（幂等语义，无实际清理收益）。
        """
        device_id = f"{ip}:{port}"
        logger.info("disconnecting_tcp", ip=ip, port=port)

        # 预检：目标不可达时 adb disconnect 会阻塞在失效 transport 清理上且无实际收益，跳过
        try:
            await probe_tcp(ip, port, settings().adb.probe_timeout)
        except DeviceUnreachableError as e:
            logger.info("tcp_disconnect_skipped_unreachable", device=device_id, reason=e.message)
            return

        try:
            await self._run("disconnect", device_id)
            logger.info("tcp_disconnected", device=device_id)
        except AdbError as e:
            logger.warning("tcp_disconnect_failed", device=device_id, error=str(e))
            # 断开失败不是严重错误，只记录警告

    async def scan_hosts(
        self,
        hosts: list[str],
        port: int = 5555,
        timeout: float | None = None,
        concurrency: int | None = None,
    ) -> list[str]:
        """
        并发探测主机列表中 TCP 端口开放的主机（方案 36）。

        返回开放主机 IP 列表（数值序）；探测细节见 reachability.scan_open_hosts。
        """
        return await scan_open_hosts(hosts, port=port, timeout=timeout, concurrency=concurrency)

    async def stream_logcat(self, device_id: str) -> AsyncIterator[str]:
        """
        逐行流式输出 logcat（修复 stderr 死锁）。

        启动长运行的 `adb shell logcat -v threadtime -T <设备当前时刻>`
        子进程并在每行到达时产出：只输出绑定时刻之后的新日志，不回放
        设备端现存缓冲。当生成器关闭时（如通过 asyncio.Task.cancel()），
        子进程被 kill。

        修复 1：启动后台任务消费 stderr，防止管道缓冲区满导致死锁。
        修复 2：-T 时间下界绑定设备端当前时刻，消除重启采集时的缓冲重放。

        参数：
            device_id: ADB 序列号。

        产出：
            每次迭代产出一行 logcat（UTF-8 字符串）。
        """
        proc = await asyncio.create_subprocess_exec(
            self.adb_path,
            "-s",
            device_id,
            "shell",
            self._LOGCAT_SINCE_NOW_CMD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
        assert proc.stdout is not None and proc.stderr is not None
        # 新增：启动后台任务消费 stderr，防止管道缓冲区满导致死锁
        stderr_task = asyncio.create_task(self._consume_stderr(proc.stderr))

        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line.decode("utf-8", errors="replace").rstrip("\r\n")
        finally:
            stderr_task.cancel()
            proc.kill()

    async def push(self, device_id: str, local: str, remote: str) -> None:
        """
        将文件从主机推送到设备。

        参数：
            device_id: ADB 序列号。
            local: 主机文件路径。
            remote: 设备文件路径。
        """
        await self._run_serial(device_id, "push", local, remote)

    async def pull(self, device_id: str, remote: str, local: str) -> None:
        """
        将文件从设备拉到主机。

        参数：
            device_id: ADB 序列号。
            remote: 设备文件路径。
            local: 主机文件路径。
        """
        await self._run_serial(device_id, "pull", remote, local)

    async def install(self, device_id: str, apk_path: str) -> None:
        """
        在设备上安装 APK（使用 -r 参数替换已存在的）。

        参数：
            device_id: ADB 序列号。
            apk_path: APK 文件的主机路径。
        """
        await self._run_serial(device_id, "install", "-r", apk_path)

    async def screenshot(self, device_id: str) -> bytes:
        """
        截取屏幕截图并返回 PNG 字节。

        使用 `adb exec-out sh -c "screencap -p 2>/dev/null"` 直接管道输出
        PNG 数据，比传统的 screencap + pull + rm 三步方式快得多（单次 ADB
        调用）。2>/dev/null 丢弃多屏设备 screencap 的 stderr 警告（方案 38
        A1）；返回前再经魔数剥前缀兜底（方案 38 A2）。

        参数：
            device_id: ADB 序列号。

        返回：
            PNG 图像数据字节。
        """
        proc = await asyncio.create_subprocess_exec(
            self.adb_path, "-s", device_id, "exec-out",
            "sh", "-c", "screencap -p 2>/dev/null",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            raise AdbError(f"Screenshot failed: {error_msg}")

        if not stdout:
            raise AdbError("Screenshot returned empty data")

        return _strip_lead_junk(stdout, PNG_MAGIC)

    async def screenshot_raw_gzip(self, device_id: str) -> bytes:
        """
        截取设备屏幕 raw 帧并经设备端 gzip -1 压缩（方案 29）。

        原理：`screencap` 无文件参数时向 stdout 输出 raw RGBA 数据
        （12/16 字节头 + 像素），管道到设备端 toybox gzip 压缩后经
        exec-out 传回——设备端 PNG 编码（~950-1000ms 单点）被换为
        gzip -1（实测照片级画面 ~640ms，spike 见方案 29 §2）。
        screencap 的 stderr 经 2>/dev/null 丢弃，返回前魔数剥前缀
        兜底（方案 38 A1/A2）。

        参数：
            device_id: ADB 序列号。

        返回：
            gzip 压缩的 raw RGBA 帧数据（含 screencap 帧头）。

        异常：
            AdbError: 执行失败（如设备端无 gzip）或输出为空时。
        """
        proc = await asyncio.create_subprocess_exec(
            self.adb_path, "-s", device_id, "exec-out",
            "sh", "-c", "screencap 2>/dev/null | gzip -1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            raise AdbError(f"Raw screenshot failed: {error_msg}")

        if not stdout:
            raise AdbError("Raw screenshot returned empty data")

        return _strip_lead_junk(stdout, GZIP_MAGIC)

    async def _consume_stderr(self, stderr: asyncio.StreamReader) -> None:
        """
        消费 stderr 防止管道缓冲区满导致死锁。

        使用场景：
          - shell_stream()：stderr 已通过 2>&1 合并，此方法作为备用
          - stream_logcat()：stderr 未合并，必须消费以防止死锁

        参数：
            stderr: 子进程的 stderr 流。
        """
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                logger.debug("adb_stderr", line=line.decode(errors="replace").strip())
        except (asyncio.CancelledError, Exception):
            pass

    async def create_shell(
        self,
        device_id: str,
        initial_output_callback: Callable[[str], None] | None = None,
    ) -> "ShellSession":
        """
        创建交互式 shell 会话（PTY 模式）。

        参数：
            device_id: ADB 序列号。
            initial_output_callback: 可选回调，接收初始输出（用于显示 prompt）。

        返回：
            ShellSession 实例（已启动）。

        异常：
            AdbError: 启动失败时。
        """
        from app.infrastructure.adb.shell import InteractiveShell
        shell = InteractiveShell()
        await shell.start(device_id, initial_output_callback)
        return shell

    def _store_recording_task(
        self,
        device_id: str,
        remote_path: str,
        bit_rate: str,
        time_limit: int,
        t: asyncio.Task[str],
    ) -> None:
        """录屏后台任务存表：挂完成回调（晚失败降档/自清）+ 记录设备端产物路径。"""
        t.add_done_callback(
            self._recording_done_callback(device_id, remote_path, bit_rate, time_limit)
        )
        self._recording_tasks[device_id] = t
        self._recording_paths[device_id] = remote_path

    def _degrade_once(
        self,
        device_id: str,
        remote_path: str,
        bit_rate: str,
        time_limit: int,
        exc: BaseException,
    ) -> asyncio.Task[str] | None:
        """
        失败降档处置公共方法（方案 37 B-Fix-2）：err=-38 降档重试一次。

        仅当失败文本含 err=-38 且本 device 本次启动尚未降过档（_degraded_once
        防重入）时，以 settings().recording.degraded_size 的 --size 参数创建
        重试后台任务并返回之；否则返回 None（调用侧决定重抛或自清）。

        同步方法（仅 create_task 不 await）：可安全用于任务完成回调内
        （回调同步执行于事件循环中）。存表/挂回调由调用侧决定时机。
        """
        if "err=-38" not in str(exc) or device_id in self._degraded_once:
            return None
        self._degraded_once.add(device_id)
        size = settings().recording.degraded_size
        retry_cmd = (
            "shell", "screenrecord",
            "--bit-rate", bit_rate,
            "--time-limit", str(time_limit),
            "--size", size,
            remote_path,
        )
        logger.warning("recording_degraded_retry", device=device_id, size=size, error=str(exc))
        return asyncio.create_task(
            self._run_serial(device_id, *retry_cmd, timeout=time_limit + 30)
        )

    async def _watch_retry_grace(
        self,
        device_id: str,
        remote_path: str,
        bit_rate: str,
        time_limit: int,
        retry: asyncio.Task[str],
        first_error: str,
    ) -> None:
        """
        看守降档重试的启动宽限窗（同首启常量）：窗内再败抛组合 AdbError
        （首启/重试双摘要，单前缀）；窗内成功或未结束 → 存表交给完成回调。
        """
        size = settings().recording.degraded_size
        for _ in range(_START_GRACE_ITERATIONS):
            await asyncio.sleep(_START_GRACE_INTERVAL)
            if not retry.done():
                continue
            exc = retry.exception()
            if exc is not None:
                # 首启/重试摘要均来自 _run（自带 "ADB command failed: " 前缀），
                # 组合时剥掉内层前缀，只保留外层的单一前缀（评审 M4 去重）
                raise AdbError(
                    f"{_ADB_FAILED_PREFIX}{_strip_adb_prefix(first_error)}; "
                    f"retry with {size} also failed: {_strip_adb_prefix(str(exc))}"
                )
            self._store_recording_task(device_id, remote_path, bit_rate, time_limit, retry)
            return
        self._store_recording_task(device_id, remote_path, bit_rate, time_limit, retry)

    async def start_recording(
        self, device_id: str, remote_path: str, bit_rate: str, time_limit: int
    ) -> None:
        """
        后台启动设备端 screenrecord 录屏（方案 37 B2）。

        通过 self._run_serial 包在 asyncio.create_task 里后台运行（不 await）；
        启动宽限 2s（_START_GRACE_ITERATIONS × _START_GRACE_INTERVAL）内观察
        成败：err=-38（编码输出缓冲不可用，常见于低端机/无硬件编码器）自动
        降档 settings().recording.degraded_size 重试一次；降档宽限内再异常抛
        AdbError（错误文本含首启与重试两份摘要）。done 且无异常视为已结束仍
        存表；非 err=-38 异常直接重抛不重试。

        方案 37 B-Fix-2：宽限窗外的「晚失败」（.25 实测 err=-38 延迟 ~3s）
        由任务完成回调兜底——回调检测异常含 err=-38 时走同一降档公共方法，
        重试任务替换任务表条目；_degraded_once 保证同一次 start 只降档一次
        （窗内已降档后回调不再二次降档）。

        参数：
            device_id: ADB 序列号。
            remote_path: 设备端输出文件路径。
            bit_rate: 码率（如 "4M"）。
            time_limit: 设备端 --time-limit 兜底时长（秒）。

        异常：
            AdbError: 非 err=-38 启动失败直接重抛；err=-38 降档重试仍失败。
        """
        # 新一次启动重置降档资格（同 device 同一次启动只降档一次）
        self._degraded_once.discard(device_id)

        cmd = (
            "shell", "screenrecord",
            "--bit-rate", bit_rate,
            "--time-limit", str(time_limit),
            remote_path,
        )
        # 必须显式传 timeout=time_limit+30：_run 的 `timeout or 配置默认 30s`
        # 回落会把 60s 录屏在默认超时处杀掉
        task = asyncio.create_task(
            self._run_serial(device_id, *cmd, timeout=time_limit + 30)
        )

        # 首启宽限窗
        for _ in range(_START_GRACE_ITERATIONS):
            await asyncio.sleep(_START_GRACE_INTERVAL)
            if not task.done():
                continue
            exc = task.exception()
            if exc is not None:
                retry = self._degrade_once(device_id, remote_path, bit_rate, time_limit, exc)
                if retry is None:
                    # 非 err=-38 或本启动已降过档：不重试，直接重抛
                    raise exc
                await self._watch_retry_grace(
                    device_id, remote_path, bit_rate, time_limit, retry, str(exc)
                )
                return
            # 已结束（无异常）：视为已结束，仍存表
            self._store_recording_task(device_id, remote_path, bit_rate, time_limit, task)
            return
        # 宽限内未结束：正常启动，后台继续录
        self._store_recording_task(device_id, remote_path, bit_rate, time_limit, task)

    def _recording_done_callback(
        self, device_id: str, remote_path: str, bit_rate: str, time_limit: int
    ) -> Callable[[asyncio.Task[str]], None]:
        """
        录屏后台任务完成回调（方案 37 B2 + B-Fix-2）。

        task.exception() 先取走未取异常（防 "Task exception was never retrieved"），
        再自清 _recording_tasks/_recording_paths 表项（防自然结束后的孤儿表项）。

        B-Fix-2 晚失败降档：异常含 err=-38 → 走 _degrade_once 公共方法，重试
        任务替换表条目（_degraded_once 防重入，同启动已降过档则不二次降档）；
        异常非 err=-38 或降档被抑制 → 打 debug 日志并自清。
        """
        def _cb(t: asyncio.Task[str]) -> None:
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                # 任务被取消无异常可取，同样自清收尾
                exc = None
            if exc is not None:
                retry = self._degrade_once(device_id, remote_path, bit_rate, time_limit, exc)
                if retry is not None:
                    # 晚失败降档：重试任务替换表条目并挂新回调，本回调不清表
                    self._store_recording_task(
                        device_id, remote_path, bit_rate, time_limit, retry
                    )
                    return
                logger.debug("recording_task_exited_with_error", device=device_id, error=str(exc))
            self._recording_tasks.pop(device_id, None)
            self._recording_paths.pop(device_id, None)

        return _cb

    async def stop_recording(self, device_id: str) -> str:
        """
        停止录屏并返回设备端产物路径（方案 37 B2）。

        主路径 `adb shell pkill -INT screenrecord`（SIGINT 触发 screenrecord
        收尾写 MP4）；pkill 无匹配（rc≠0 且 stderr 空）不算错，返回空串表示
        无产物。pkill 不可用/异常时 toybox 兜底：pidof screenrecord 取 pid，
        非空则 `kill -INT <pid>`，pid 为空返回空串。收尾信号发出后等待后台
        任务退出（try/except 捕一切异常——SIGINT 收尾 MP4 时 screenrecord
        可能非零退出，不算错）并返回 start 时记录的 remote_path。

        参数：
            device_id: ADB 序列号。

        返回：
            设备端录屏文件路径；无活跃录制或无产物时返回空串。
        """
        task = self._recording_tasks.get(device_id)
        remote_path = self._recording_paths.get(device_id, "")
        if task is None:
            return ""

        try:
            await self._run_serial(device_id, "shell", "pkill", "-INT", "screenrecord")
        except AdbError as e:
            if str(e).endswith("failed: "):
                # rc≠0 且 stderr 空 = 无匹配进程：无产物，不算错
                logger.debug("screenrecord_not_running", device=device_id)
                return ""
            # pkill 不可用/异常 → toybox 兜底：pidof 取 pid
            pid = ""
            try:
                pid = (
                    await self._run_serial(device_id, "shell", "pidof", "screenrecord")
                ).strip()
            except AdbError:
                logger.debug("pidof_screenrecord_failed", device=device_id)
            if not pid:
                return ""
            try:
                await self._run_serial(device_id, "shell", "kill", "-INT", pid)
            except AdbError:
                logger.debug("kill_int_failed", device=device_id, pid=pid)

        # 等后台录屏任务收尾——SIGINT 收尾 MP4 时 screenrecord 可能非零退出，
        # 一切异常都不算错（异常已由 await/回调取走，不会产生未取告警）
        try:
            await task
        except BaseException as exc:
            logger.debug("recording_task_ended", device=device_id, error=str(exc))
        return remote_path

    async def pull_recording(
        self, device_id: str, remote_path: str, local_dir: str
    ) -> str | None:
        """
        将设备端录屏文件拉取到主机目录（方案 37 B2）。

        `adb pull <remote> <local_dir>`，成功返回
        os.path.join(local_dir, basename(remote_path))；远程文件不存在
        （AdbError 消息含 "No such file" / "does not exist"）返回 None，
        其他错误重抛。

        参数：
            device_id: ADB 序列号。
            remote_path: 设备端文件路径。
            local_dir: 主机落盘目录。

        返回：
            成功时本地文件路径；远程不存在时 None。

        异常：
            AdbError: 远程不存在特征以外的拉取失败。
        """
        try:
            await self._run_serial(device_id, "pull", remote_path, local_dir)
        except AdbError as e:
            if "No such file" in str(e) or "does not exist" in str(e):
                logger.debug("pull_recording_no_such_file", device=device_id, remote=remote_path)
                return None
            raise
        return os.path.join(local_dir, os.path.basename(remote_path))

    async def rm_remote(self, device_id: str, remote_path: str) -> None:
        """
        设备端删除文件（方案 37 B2）。

        `adb shell rm -f <remote>`，设备端即清；失败仅打警告不抛
        （幂等语义，删除失败不是严重错误）。

        参数：
            device_id: ADB 序列号。
            remote_path: 设备端文件路径。
        """
        try:
            await self._run_serial(device_id, "shell", "rm", "-f", remote_path)
        except AdbError:
            logger.warning("rm_remote_failed", device=device_id, remote=remote_path)

    async def get_runtime_info(self, device_id: str) -> RuntimeInfo:
        """
        查询设备应用运行时状态（方案 37 C2）。

        三条 dumpsys 子命令独立并发（模式同 get_device_info）——单台总耗时上界
        压到 max(单条) ≤ _RUNTIME_CMD_TIMEOUT。return_exceptions=True 必不可少：
        首异常取消其余命令会跳过 _run 的超时 kill 收尾路径，留下孤儿 adb 进程。
        activity/services 失败按「整查询失败」语义冒泡；providers 例外——
        旧设备子命令不支持时整份静默置空清单（本次三机实测 7.1.2 亦完整返回，
        见 tests/application/fixtures/README.md）。

        参数：
            device_id: ADB 序列号。

        返回：
            填充了 pkg/top_activity/resumed/services/providers 的 RuntimeInfo；
            解析不到的行锚定字段为空串、清单为空列表。

        异常：
            AdbError: activity 或 services 命令失败或超时时（文本含命令名）。
        """
        results = await asyncio.gather(
            self._run_serial(
                device_id, "shell", "dumpsys", "activity",
                timeout=_RUNTIME_CMD_TIMEOUT,
            ),
            self._run_serial(
                device_id, "shell", "dumpsys", "activity", "services",
                timeout=_RUNTIME_CMD_TIMEOUT,
            ),
            self._run_serial(
                device_id, "shell", "dumpsys", "activity", "providers",
                timeout=_RUNTIME_CMD_TIMEOUT,
            ),
            return_exceptions=True,
        )
        activity_out, services_out, providers_out = results

        # 三份结果分别判断（不能照抄 get_device_info 的统一 raise-first）：
        # providers 异常仅置空清单，activity/services 异常冒泡且文本含命令名
        if isinstance(activity_out, BaseException):
            raise AdbError(f"dumpsys activity 查询失败: {activity_out}")
        if isinstance(services_out, BaseException):
            raise AdbError(f"dumpsys activity services 查询失败: {services_out}")
        providers: list[str] = []
        if not isinstance(providers_out, BaseException):
            providers = parse_providers(providers_out)

        # 异常已在上方逐一排除，mypy 沿 isinstance 分支收窄，剩余元素
        # 类型由各命令签名保证（此处无需再 cast）
        activity = parse_activity_runtime(activity_out)
        services = parse_services(services_out)

        return RuntimeInfo(
            pkg=activity.pkg,
            top_activity=activity.top_activity,
            resumed=activity.resumed,
            services=services,
            providers=providers,
        )

    async def get_cpu_usage(self, device_id: str) -> list[CpuProcessInfo]:
        """
        查询设备 CPU 使用率进程级明细（方案 37 D2）。

        单命令 `adb shell dumpsys cpuinfo`（不做 gather——D3 采样循环自有
        布局），输出交 parse_cpuinfo 解析；`CPU usage from` 头行缺失或
        进程段为空 → 空列表。cpuinfo 输出量小，超时沿用快命令口径
        （_INFO_CMD_TIMEOUT）。

        参数：
            device_id: ADB 序列号。

        返回：
            CpuProcessInfo 清单；无进程时为空列表。

        异常：
            AdbError: 命令失败或超时（调用方 D3 容错）。
        """
        output = await self._run_serial(
            device_id, "shell", "dumpsys", "cpuinfo", timeout=self._INFO_CMD_TIMEOUT
        )
        return parse_cpuinfo(output)

    async def get_battery_info(self, device_id: str) -> BatteryInfo:
        """
        查询设备电池四字段（方案 37 D2）。

        单命令 `adb shell dumpsys battery`，输出交 parse_battery 解析
        （level/temperature/voltage/health；缺字段 None/0 不抛）。
        整命令 AdbError 不吞、向上抛（调用方 D3 容错）。

        参数：
            device_id: ADB 序列号。

        返回：
            BatteryInfo；缺失字段为 None（level → 0）。

        异常：
            AdbError: 命令失败或超时。
        """
        output = await self._run_serial(
            device_id, "shell", "dumpsys", "battery", timeout=self._INFO_CMD_TIMEOUT
        )
        return parse_battery(output)
