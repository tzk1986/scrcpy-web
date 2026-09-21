"""
应用管理服务测试
==================

测试 AppService（backend/app/application/app_service.py）：

    - `dumpsys package` 批量解析（版本 / 安装时间 / 系统标志）
    - `dumpsys activity processes` 运行进程解析（含去重与兜底）
    - list_apps 列表组装（包列表 + dumpsys + 运行进程合并）
    - get_app_info 详情（APK 路径 / 大小、运行状态、内存）
    - launch / stop / uninstall / clear 命令组装与成功失败两条路径

全部使用手写 FakeAdb（按精确命令行返回预设输出并记录调用序列），不依赖真实设备。
"""

from app.application.app_service import AppService
from app.core.exceptions import AdbError

DEVICE = "192.168.1.100"
CHAT_PKG = "com.example.chat"
SYS_PKG = "com.example.systemtool"
CHAT_APK = f"/data/app/~~xyz==/{CHAT_PKG}-abc==/base.apk"
# 第三方包列表解析用的旧版路径格式（Android 10 及更早，不含 "="）。
# Android 11+ 路径形如 /data/app/~~xxx==/pkg-yyy==/base.apk（含 "="），
# list_apps 会解析错误 —— 见 test_list_apps_misparses_apk_path_containing_equals。
LEGACY_CHAT_APK = f"/data/app/{CHAT_PKG}-1/base.apk"
MONKEY_CHAT_CMD = (
    f"monkey -p {CHAT_PKG} -c android.intent.category.LAUNCHER --pct-syskeys 0 1"
)


# ---------------------------------------------------------------------------
# 测试替身与数据构造
# ---------------------------------------------------------------------------

class FakeAdb:
    """按完整命令行匹配预设输出、可注入失败、记录调用序列的假 ADB 驱动。"""

    def __init__(self):
        self.outputs: dict[str, str] = {}
        self.fail_patterns: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def on(self, cmd: str, output: str) -> "FakeAdb":
        """预设命令 cmd 的完整输出（未预设的命令返回空串）。"""
        self.outputs[cmd] = output
        return self

    def fail_on(self, substring: str) -> "FakeAdb":
        """命中子串的命令抛 AdbError；传空串表示所有命令失败。"""
        self.fail_patterns.append(substring)
        return self

    async def shell(self, device_id: str, cmd: str) -> str:
        self.calls.append((device_id, cmd))
        if any(pat in cmd for pat in self.fail_patterns):
            raise AdbError(f"ADB command failed: {cmd}")
        return self.outputs.get(cmd, "")

    @property
    def cmds(self) -> list[str]:
        """已执行命令列表（按调用顺序，不含设备 ID）。"""
        return [cmd for _device_id, cmd in self.calls]


def _pm_list_line(package: str, apk_path: str) -> str:
    """构造一行 `pm list packages -f` 输出。"""
    return f"package:{apk_path}={package}"


def _dumpsys_block(
    package: str,
    *,
    version_code: int = 1,
    version_name: str = "1.0",
    flags: str = " HAS_CODE ALLOW_CLEAR_USER_DATA ",
) -> str:
    """构造 `dumpsys package` 中一个包的信息块（模仿真机输出格式）。"""
    return (
        f"  Package [{package}] (a1b2c3d):\n"
        "    userId=10123\n"
        f"    codePath=/data/app/~~xyz==/{package}-abc==/base.apk\n"
        f"    versionCode={version_code} minSdk=24 targetSdk=34\n"
        f"    versionName={version_name}\n"
        f"    flags=[{flags}]\n"
        f"    pkgFlags=[{flags}]\n"
        "    firstInstallTime=2025-11-02 10:14:33\n"
        "    lastUpdateTime=2026-03-18 09:05:12\n"
    )


DUMPSYS_ALL_PACKAGES = (
    "ACTIVITY MANAGER PACKAGES (dumpsys package)\n"
    "Packages:\n"
    + _dumpsys_block(CHAT_PKG, version_code=305, version_name="3.5.0")
    + _dumpsys_block(SYS_PKG, version_code=12, version_name="1.2", flags=" SYSTEM HAS_CODE ")
)

DUMPSYS_CHAT = (
    f"ACTIVITY MANAGER PACKAGES (dumpsys package {CHAT_PKG})\n"
    "Packages:\n"
    + _dumpsys_block(CHAT_PKG, version_code=305, version_name="3.5.0")
)

PROCESSES_WITH_CHAT = (
    "ACTIVITY MANAGER RUNNING PROCESSES (dumpsys activity processes)\n"
    "  *APP* UID 10123 ProcessRecord{a1b2c3d 1335:com.example.chat/u0a123}\n"
    "  *APP* UID 1000 ProcessRecord{9d1c2b3 2456:com.example.systemtool/u0a45}\n"
)

PROCESSES_WITHOUT_CHAT = (
    "ACTIVITY MANAGER RUNNING PROCESSES (dumpsys activity processes)\n"
    "  *APP* UID 1000 ProcessRecord{9d1c2b3 2456:com.example.systemtool/u0a45}\n"
)

LS_L_CHAT = f"-rw-r--r-- 1 system system 68456117 2026-08-04 19:16 {CHAT_APK}\n"

# 真机（192.168.8.18，Android 11+）dumpsys meminfo 输出片段
MEMINFO_CHAT = (
    "Applications Memory Usage (in Kilobytes):\n"
    "\n"
    "** MEMINFO in pid 4023 [com.example.chat] **\n"
    "                   Pss  Private  Private  SwapPss      Rss     Heap     Heap     Heap\n"
    "                 Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free\n"
    "                ------   ------   ------   ------   ------   ------   ------   ------\n"
    "  Native Heap    22912    22860       24       59    23780    32888    24682     4767\n"
    "        TOTAL    75784    59528     6472      392    75784    72259    55867    12953\n"
    "           TOTAL PSS:    75784            TOTAL RSS:   203600       "
    "TOTAL SWAP PSS:      392\n"
)


# ---------------------------------------------------------------------------
# _parse_dumpsys_packages
# ---------------------------------------------------------------------------

def test_parse_dumpsys_packages_extracts_fields_and_system_flag():
    """多个包块：版本、安装/更新时间、系统标志逐个解析且互不串块。"""
    service = AppService(FakeAdb())

    parsed = service._parse_dumpsys_packages(DUMPSYS_ALL_PACKAGES)

    assert set(parsed) == {CHAT_PKG, SYS_PKG}
    chat = parsed[CHAT_PKG]
    assert chat["version_name"] == "3.5.0"
    assert chat["version_code"] == 305
    assert chat["install_time"] == "2025-11-02"
    assert chat["update_time"] == "2026-03-18"
    assert chat["is_system"] is False
    assert parsed[SYS_PKG]["version_name"] == "1.2"
    assert parsed[SYS_PKG]["is_system"] is True


def test_parse_dumpsys_packages_missing_fields_use_defaults():
    """块内缺少 versionName/versionCode/时间时使用默认值。"""
    service = AppService(FakeAdb())
    output = (
        "Packages:\n"
        "  Package [com.minimal] (ff00):\n"
        "    userId=10000\n"
        "    codePath=/system/app/Minimal/Minimal.apk\n"
    )

    parsed = service._parse_dumpsys_packages(output)

    assert parsed == {
        "com.minimal": {
            "version_name": "",
            "version_code": 0,
            "install_time": "",
            "update_time": "",
            "is_system": False,
        }
    }


def test_parse_dumpsys_packages_without_headers_returns_empty():
    """没有 Package [ 头部的输出（空串/仅标题）解析为空字典。"""
    service = AppService(FakeAdb())

    assert service._parse_dumpsys_packages("") == {}
    assert service._parse_dumpsys_packages("Packages:\n  userId=1000\n") == {}


def test_parse_dumpsys_packages_truncates_block_before_unconsumed_header():
    """块内残留未闭合的下一个头部时截断，不误取后续包的字段。"""
    service = AppService(FakeAdb())
    output = (
        "  Package [com.a] (a1):\n"
        "    versionCode=1\n"
        "  Package [com.b\n"  # 头部未闭合 → re.split 不会消费它
        "    versionName=9.9\n"
    )

    parsed = service._parse_dumpsys_packages(output)

    assert parsed["com.a"]["version_code"] == 1
    assert parsed["com.a"]["version_name"] == ""  # 截断后未误取 9.9


# ---------------------------------------------------------------------------
# _get_running_processes
# ---------------------------------------------------------------------------

async def test_get_running_processes_parses_pid_and_keeps_first_duplicate():
    """解析 ProcessRecord（含大写 hex、无用户后缀），重复包保留首个 PID。"""
    fake = FakeAdb().on(
        "dumpsys activity processes",
        "ACTIVITY MANAGER RUNNING PROCESSES (dumpsys activity processes)\n"
        "  *APP* UID 10123 ProcessRecord{a1b2c3d 1335:com.example.chat/u0a123}\n"
        "  *APP* UID 1000 ProcessRecord{9D1C2B3 2456:com.example.systemtool/u0a45}\n"
        "  ProcessRecord{ffff0000 9999:com.example.chat/u0a123}\n"
        "  Proc{abc123 4242:com.ignored/u0a1}\n",
    )
    service = AppService(fake)

    result = await service._get_running_processes(DEVICE)

    assert result == {CHAT_PKG: 1335, SYS_PKG: 2456}
    assert fake.cmds == ["dumpsys activity processes"]


async def test_get_running_processes_returns_empty_on_adb_error():
    """命令抛错时兜底返回空字典。"""
    service = AppService(FakeAdb().fail_on("dumpsys activity processes"))

    assert await service._get_running_processes(DEVICE) == {}


# ---------------------------------------------------------------------------
# list_apps
# ---------------------------------------------------------------------------

async def test_list_apps_merges_dumpsys_and_processes():
    """第三方列表：三条命令各一次，解析版本/时间/系统标志并合并运行 PID。"""
    fake = FakeAdb()
    fake.on("pm list packages -f -3", _pm_list_line(CHAT_PKG, LEGACY_CHAT_APK) + "\n")
    fake.on("dumpsys package", DUMPSYS_ALL_PACKAGES)
    fake.on("dumpsys activity processes", PROCESSES_WITH_CHAT)

    apps = await AppService(fake).list_apps(DEVICE)

    assert fake.cmds == [
        "pm list packages -f -3",
        "dumpsys package",
        "dumpsys activity processes",
    ]
    assert all(dev == DEVICE for dev, _cmd in fake.calls)
    assert len(apps) == 1
    app = apps[0]
    assert app.package_name == CHAT_PKG
    assert app.version_name == "3.5.0"
    assert app.version_code == 305
    assert app.install_time == "2025-11-02"
    assert app.update_time == "2026-03-18"
    assert app.is_system is False
    assert app.is_running is True
    assert app.pid == 1335
    assert app.apk_size_mb == 0.0  # 列表页不查大小
    assert app.memory_kb is None  # 列表页不查内存


async def test_list_apps_include_system_lists_system_packages():
    """include_system=True 使用不带 -3 的包列表命令，并保留系统应用标志。"""
    fake = FakeAdb()
    fake.on(
        "pm list packages -f",
        _pm_list_line(CHAT_PKG, LEGACY_CHAT_APK)
        + "\n"
        + _pm_list_line(SYS_PKG, f"/system/priv-app/{SYS_PKG}/{SYS_PKG}.apk")
        + "\n",
    )
    fake.on("dumpsys package", DUMPSYS_ALL_PACKAGES)
    fake.on("dumpsys activity processes", PROCESSES_WITHOUT_CHAT)

    apps = await AppService(fake).list_apps(DEVICE, include_system=True)

    assert fake.cmds[0] == "pm list packages -f"
    by_name = {app.package_name: app for app in apps}
    assert sorted(by_name) == sorted([CHAT_PKG, SYS_PKG])
    assert by_name[SYS_PKG].is_system is True
    assert by_name[SYS_PKG].is_running is True
    assert by_name[SYS_PKG].pid == 2456
    assert by_name[CHAT_PKG].is_running is False
    assert by_name[CHAT_PKG].pid is None


async def test_list_apps_returns_empty_without_valid_package_lines():
    """输出中没有合法的 package:路径=包名 行时直接返回空列表（不再查 dumpsys）。"""
    fake = FakeAdb().on(
        "pm list packages -f -3",
        "package:com.broken.no.path\nnot a package line\n",
    )

    apps = await AppService(fake).list_apps(DEVICE)

    assert apps == []
    assert fake.cmds == ["pm list packages -f -3"]


async def test_list_apps_returns_empty_on_adb_error():
    """包列表命令失败时兜底返回空列表。"""
    fake = FakeAdb().fail_on("")

    assert await AppService(fake).list_apps(DEVICE) == []


async def test_list_apps_misparses_apk_path_containing_equals():
    """已知源码 bug（真机 192.168.8.18 实测）：Android 11+ 的 APK 路径含 "=="
    （如 /data/app/~~VCcmZr4z...==/pkg-...==/base.apk），`package:(.+?)=(.+)`
    的懒惰匹配把路径中第一个 "=" 当作分隔符 → 包名/路径解析出错。
    本测试锁定当前行为，修复源码后需同步更新（详见测试报告）。
    """
    real_device_line = (
        "package:/data/app/~~VCcmZr4z-HepZhwWBNl_YA==/com.bjw.ComAssistant-"
        "YHI0fR0X9Q-6p8VHWcvM2g==/base.apk=com.bjw.ComAssistant"
    )
    fake = FakeAdb().on("pm list packages -f -3", real_device_line + "\n")

    apps = await AppService(fake).list_apps(DEVICE)

    bad_package_name = (
        "=/com.bjw.ComAssistant-YHI0fR0X9Q-6p8VHWcvM2g==/base.apk=com.bjw.ComAssistant"
    )
    assert apps[0].package_name == bad_package_name
    assert apps[0].version_name == ""  # 包名错位后 dumpsys 信息也对不上


# ---------------------------------------------------------------------------
# get_app_info / _get_app_info_internal
# ---------------------------------------------------------------------------

async def test_get_app_info_parses_running_app_details():
    """运行中的应用：APK 路径/大小、版本、运行 PID、内存全部解析正确。"""
    fake = FakeAdb()
    fake.on(f"pm list packages -f {CHAT_PKG}", _pm_list_line(CHAT_PKG, CHAT_APK) + "\n")
    fake.on(f"dumpsys package {CHAT_PKG}", DUMPSYS_CHAT)
    fake.on(f"ls -l {CHAT_APK}", LS_L_CHAT)
    fake.on("dumpsys activity processes", PROCESSES_WITH_CHAT)
    fake.on(f"dumpsys meminfo {CHAT_PKG}", MEMINFO_CHAT)

    info = await AppService(fake).get_app_info(DEVICE, CHAT_PKG)

    assert fake.cmds == [
        f"pm list packages -f {CHAT_PKG}",
        f"dumpsys package {CHAT_PKG}",
        f"ls -l {CHAT_APK}",
        "dumpsys activity processes",
        f"dumpsys meminfo {CHAT_PKG}",
    ]
    assert info is not None
    assert info.package_name == CHAT_PKG
    assert info.version_name == "3.5.0"
    assert info.version_code == 305
    assert info.install_time == "2025-11-02"
    assert info.update_time == "2026-03-18"
    assert info.apk_size_mb == 65.28  # 68456117 字节
    assert info.is_system is False
    assert info.is_running is True
    assert info.pid == 1335
    assert info.memory_kb == 75784


async def test_get_app_info_not_running_skips_meminfo():
    """未运行时 is_running=False、pid/memory 为 None，不调用 dumpsys meminfo。"""
    fake = FakeAdb()
    fake.on(f"pm list packages -f {CHAT_PKG}", _pm_list_line(CHAT_PKG, CHAT_APK) + "\n")
    fake.on(f"dumpsys package {CHAT_PKG}", DUMPSYS_CHAT)
    fake.on(f"ls -l {CHAT_APK}", LS_L_CHAT)
    fake.on("dumpsys activity processes", PROCESSES_WITHOUT_CHAT)

    info = await AppService(fake).get_app_info(DEVICE, CHAT_PKG)

    assert info is not None
    assert info.is_running is False
    assert info.pid is None
    assert info.memory_kb is None
    assert f"dumpsys meminfo {CHAT_PKG}" not in fake.cmds


async def test_get_app_info_absent_package_returns_empty_fields():
    """包不存在（各命令输出为空）时返回字段为空的 AppInfo，而非 None。"""
    fake = FakeAdb()  # 所有命令返回空输出

    info = await AppService(fake).get_app_info(DEVICE, "com.ghost.app")

    assert info is not None
    assert info.package_name == "com.ghost.app"
    assert info.version_name == ""
    assert info.version_code == 0
    assert info.install_time == ""
    assert info.update_time == ""
    assert info.apk_size_mb == 0.0
    assert info.is_running is False
    assert info.memory_kb is None
    # 无 APK 路径时不执行 ls -l
    assert not any(cmd.startswith("ls -l") for cmd in fake.cmds)


async def test_get_app_info_detects_system_app():
    """dumpsys 含 pkgFlags 且带 "[ SYSTEM ]" 子串时判定为系统应用。"""
    fake = FakeAdb()
    fake.on(
        f"pm list packages -f {SYS_PKG}",
        _pm_list_line(SYS_PKG, f"/system/priv-app/{SYS_PKG}/base.apk") + "\n",
    )
    fake.on(f"dumpsys package {SYS_PKG}", _dumpsys_block(SYS_PKG, flags=" SYSTEM "))
    fake.on("dumpsys activity processes", PROCESSES_WITHOUT_CHAT)

    info = await AppService(fake).get_app_info(DEVICE, SYS_PKG)

    assert info is not None
    assert info.is_system is True
    assert info.is_running is True  # 运行但 meminfo 输出为空 → 内存为 None
    assert info.memory_kb is None


async def test_get_app_info_multi_flag_system_pkg_not_detected_as_system():
    """已知源码 bug（真机 192.168.8.18 实测）：is_system 要求子串 "[ SYSTEM ]"，
    而真机输出为 "pkgFlags=[ SYSTEM HAS_CODE ALLOW_BACKUP ... ]" → 恒判为 False。
    本测试锁定当前行为，修复源码后需同步更新（详见测试报告）。
    """
    fake = FakeAdb().on(
        f"dumpsys package {SYS_PKG}",
        _dumpsys_block(
            SYS_PKG,
            flags=" SYSTEM HAS_CODE ALLOW_BACKUP KILL_AFTER_RESTORE RESTORE_ANY_VERSION ",
        ),
    )

    info = await AppService(fake).get_app_info(DEVICE, SYS_PKG)

    assert info is not None
    assert info.is_system is False


async def test_get_app_info_returns_none_when_dumpsys_fails():
    """dumpsys package 抛错 → 整体兜底返回 None。"""
    fake = FakeAdb().fail_on(f"dumpsys package {CHAT_PKG}")

    assert await AppService(fake).get_app_info(DEVICE, CHAT_PKG) is None


async def test_get_app_info_internal_reuses_given_apk_path():
    """传入 apk_path 时跳过 `pm list packages -f`，直接查大小。"""
    fake = FakeAdb()
    fake.on(f"dumpsys package {CHAT_PKG}", DUMPSYS_CHAT)
    fake.on(f"ls -l {CHAT_APK}", LS_L_CHAT)
    fake.on("dumpsys activity processes", PROCESSES_WITH_CHAT)

    info = await AppService(fake)._get_app_info_internal(DEVICE, CHAT_PKG, apk_path=CHAT_APK)

    assert info is not None
    assert f"pm list packages -f {CHAT_PKG}" not in fake.cmds
    assert info.apk_size_mb == 65.28


async def test_get_app_info_apk_size_zero_when_ls_fails():
    """ls -l 抛错只影响 APK 大小（0.0），其余字段仍正常返回。"""
    fake = FakeAdb()
    fake.on(f"pm list packages -f {CHAT_PKG}", _pm_list_line(CHAT_PKG, CHAT_APK) + "\n")
    fake.on(f"dumpsys package {CHAT_PKG}", DUMPSYS_CHAT)
    fake.fail_on(f"ls -l {CHAT_APK}")
    fake.on("dumpsys activity processes", PROCESSES_WITHOUT_CHAT)

    info = await AppService(fake).get_app_info(DEVICE, CHAT_PKG)

    assert info is not None
    assert info.apk_size_mb == 0.0
    assert info.version_name == "3.5.0"


async def test_get_app_info_apk_size_zero_when_ls_output_unparsable():
    """ls -l 输出无法解析（文件不存在）时 APK 大小为 0.0。"""
    fake = FakeAdb()
    fake.on(f"pm list packages -f {CHAT_PKG}", _pm_list_line(CHAT_PKG, CHAT_APK) + "\n")
    fake.on(f"dumpsys package {CHAT_PKG}", DUMPSYS_CHAT)
    fake.on(f"ls -l {CHAT_APK}", f"ls: {CHAT_APK}: No such file or directory\n")

    info = await AppService(fake).get_app_info(DEVICE, CHAT_PKG)

    assert info is not None
    assert info.apk_size_mb == 0.0


# ---------------------------------------------------------------------------
# launch_app / stop_app / uninstall_app / clear_app_data
# ---------------------------------------------------------------------------

async def test_launch_app_injects_event_success():
    """monkey 输出含 "Events injected: 1" → 启动成功。"""
    fake = FakeAdb().on(
        MONKEY_CHAT_CMD,
        "Events injected: 1\n## Network stats: elapsed time=85ms\n",
    )

    assert await AppService(fake).launch_app(DEVICE, CHAT_PKG) is True
    assert fake.cmds == [MONKEY_CHAT_CMD]


async def test_launch_app_returns_false_without_event():
    """monkey 未注入事件（无可启动 Activity）→ 返回 False。"""
    fake = FakeAdb().on(MONKEY_CHAT_CMD, "** No activities found to run, monkey aborted.\n")

    assert await AppService(fake).launch_app(DEVICE, CHAT_PKG) is False


async def test_launch_app_returns_false_on_adb_error():
    """命令抛错 → 返回 False。"""
    fake = FakeAdb().fail_on("monkey")

    assert await AppService(fake).launch_app(DEVICE, CHAT_PKG) is False


async def test_stop_app_force_stops_and_returns_true():
    """force-stop 命令组装正确，命令未抛错即视为成功。"""
    fake = FakeAdb()

    assert await AppService(fake).stop_app(DEVICE, CHAT_PKG) is True
    assert fake.cmds == [f"am force-stop {CHAT_PKG}"]


async def test_stop_app_returns_false_on_adb_error():
    """命令抛错 → 返回 False。"""
    fake = FakeAdb().fail_on("am force-stop")

    assert await AppService(fake).stop_app(DEVICE, CHAT_PKG) is False


async def test_uninstall_app_success():
    """pm uninstall 输出 "Success" → True，命令组装正确。"""
    fake = FakeAdb().on(f"pm uninstall {CHAT_PKG}", "Success\n")

    assert await AppService(fake).uninstall_app(DEVICE, CHAT_PKG) is True
    assert fake.cmds == [f"pm uninstall {CHAT_PKG}"]


async def test_uninstall_app_failure_output():
    """pm uninstall 输出 Failure → False。"""
    fake = FakeAdb().on(f"pm uninstall {CHAT_PKG}", "Failure [DELETE_FAILED_INTERNAL_ERROR]\n")

    assert await AppService(fake).uninstall_app(DEVICE, CHAT_PKG) is False


async def test_uninstall_app_returns_false_on_adb_error():
    """命令抛错 → 返回 False。"""
    fake = FakeAdb().fail_on("pm uninstall")

    assert await AppService(fake).uninstall_app(DEVICE, CHAT_PKG) is False


async def test_clear_app_data_success():
    """pm clear 输出 "Success" → True，命令组装正确。"""
    fake = FakeAdb().on(f"pm clear {CHAT_PKG}", "Success\n")

    assert await AppService(fake).clear_app_data(DEVICE, CHAT_PKG) is True
    assert fake.cmds == [f"pm clear {CHAT_PKG}"]


async def test_clear_app_data_failure_output():
    """pm clear 输出 Failed → False。"""
    fake = FakeAdb().on(f"pm clear {CHAT_PKG}", "Failed\n")

    assert await AppService(fake).clear_app_data(DEVICE, CHAT_PKG) is False


async def test_clear_app_data_returns_false_on_adb_error():
    """命令抛错 → 返回 False。"""
    fake = FakeAdb().fail_on("pm clear")

    assert await AppService(fake).clear_app_data(DEVICE, CHAT_PKG) is False


# ---------------------------------------------------------------------------
# _get_apk_path / _check_app_running / _get_app_memory
# ---------------------------------------------------------------------------

async def test_get_apk_path_matches_queried_package():
    """多行输出中按包名精确匹配出 APK 路径。"""
    fake = FakeAdb().on(
        f"pm list packages -f {CHAT_PKG}",
        _pm_list_line("com.example.other", "/data/app/other/base.apk")
        + "\n"
        + _pm_list_line(CHAT_PKG, CHAT_APK)
        + "\n",
    )

    assert await AppService(fake)._get_apk_path(DEVICE, CHAT_PKG) == CHAT_APK
    assert fake.cmds == [f"pm list packages -f {CHAT_PKG}"]


async def test_get_apk_path_returns_none_when_not_listed():
    """输出中没有该包 → None。"""
    fake = FakeAdb().on(f"pm list packages -f {CHAT_PKG}", "package:/data/app/other/base.apk=x\n")

    assert await AppService(fake)._get_apk_path(DEVICE, CHAT_PKG) is None


async def test_get_apk_path_returns_none_on_adb_error():
    """命令抛错 → None。"""
    fake = FakeAdb().fail_on("pm list packages -f")

    assert await AppService(fake)._get_apk_path(DEVICE, CHAT_PKG) is None


async def test_check_app_running_finds_pid_with_user_suffix():
    """ProcessRecord 带 /u0a123 后缀（大写 hex）→ (True, pid)。"""
    fake = FakeAdb().on(
        "dumpsys activity processes",
        "  *APP* UID 10123 ProcessRecord{A1B2C3D 1335:com.example.chat/u0a123}\n",
    )

    assert await AppService(fake)._check_app_running(DEVICE, CHAT_PKG) == (True, 1335)


async def test_check_app_running_finds_pid_without_user_suffix():
    """ProcessRecord 无用户后缀 → (True, pid)。"""
    fake = FakeAdb().on(
        "dumpsys activity processes",
        "  ProcessRecord{abc123 1335:com.example.chat}\n",
    )

    assert await AppService(fake)._check_app_running(DEVICE, CHAT_PKG) == (True, 1335)


async def test_check_app_running_ignores_prefix_package():
    """同前缀包（com.example.chat.debug）不算命中 → (False, None)。"""
    fake = FakeAdb().on(
        "dumpsys activity processes",
        "  ProcessRecord{abc123 1335:com.example.chat.debug/u0a123}\n"
        "  ProcessRecord{abc123 4242:com.example.chatter/u0a123}\n",
    )

    assert await AppService(fake)._check_app_running(DEVICE, CHAT_PKG) == (False, None)


async def test_check_app_running_returns_false_on_adb_error():
    """命令抛错 → (False, None)。"""
    fake = FakeAdb().fail_on("dumpsys activity processes")

    assert await AppService(fake)._check_app_running(DEVICE, CHAT_PKG) == (False, None)


async def test_get_app_memory_total_colon_format():
    """旧版（Android 10 及更早）格式：行首 `TOTAL: 6968`。"""
    fake = FakeAdb().on(
        f"dumpsys meminfo {CHAT_PKG}",
        "** MEMINFO in pid 1335 [com.example.chat] **\n"
        "                   Pss  Private\n"
        "          TOTAL:   6968     5000\n",
    )

    assert await AppService(fake)._get_app_memory(DEVICE, CHAT_PKG) == 6968


async def test_get_app_memory_uppercase_total_pss_format():
    """真机（Android 11+）格式：`TOTAL PSS:    75784`。"""
    fake = FakeAdb().on(f"dumpsys meminfo {CHAT_PKG}", MEMINFO_CHAT)

    assert await AppService(fake)._get_app_memory(DEVICE, CHAT_PKG) == 75784


async def test_get_app_memory_titlecase_total_pss_format():
    """格式 3：`Total PSS: 12345 kB`（大小写不一致）。"""
    fake = FakeAdb().on(
        f"dumpsys meminfo {CHAT_PKG}",
        "** MEMINFO in pid 1335 [com.example.chat] **\nTotal PSS: 12345 kB\n",
    )

    assert await AppService(fake)._get_app_memory(DEVICE, CHAT_PKG) == 12345


async def test_get_app_memory_returns_none_when_unparsable():
    """输出没有任何已知格式 → None。"""
    fake = FakeAdb().on(f"dumpsys meminfo {CHAT_PKG}", "No process found for: com.example.chat\n")

    assert await AppService(fake)._get_app_memory(DEVICE, CHAT_PKG) is None


async def test_get_app_memory_returns_none_on_adb_error():
    """命令抛错 → None。"""
    fake = FakeAdb().fail_on("dumpsys meminfo")

    assert await AppService(fake)._get_app_memory(DEVICE, CHAT_PKG) is None


async def test_get_app_memory_public_method_delegates():
    """公开方法 get_app_memory 与内部实现返回一致。"""
    fake = FakeAdb().on(f"dumpsys meminfo {CHAT_PKG}", MEMINFO_CHAT)

    assert await AppService(fake).get_app_memory(DEVICE, CHAT_PKG) == 75784