"""
依赖注入容器
=============

层：横切关注点（启动引导）。

本模块是依赖注入的组合根。它将领域端口协议（Protocol）连接到
具体的基础设施实现，并以单例形式缓存。

装配规则：
    - 单例服务（driver、repository、encoder）使用 @lru_cache()。
    - 瞬态服务（DeviceService、DebugService 等）每次调用时新建，
      但其依赖项是单例。
    - FastAPI 的 Depends() 在路由处理器中调用这些函数。

依赖关系图：
    AdbCliDriver           ← 实现 AdbDriver（Protocol）
    SqliteDeviceRepository ← 实现 DeviceRepository（Protocol）
    SqliteDebugRepository  ← 实现 DebugRepository（Protocol）
    ScrcpyEncoder          ← 实现 VideoEncoder（Protocol）

    DeviceService(adb, repo)
    DebugService(adb, repo)
    StreamService(encoder)
    SessionService()（无依赖，纯内存）
"""

from functools import lru_cache

from app.application.debug_service import DebugService
from app.application.device_service import DeviceService
from app.application.session_service import SessionService
from app.application.stream_service import StreamService
from app.domain.ports import AdbDriver, DebugRepository, DeviceRepository, VideoEncoder
from app.infrastructure.adb.cli import AdbCliDriver
from app.infrastructure.persistence.sqlite import SqliteDebugRepository, SqliteDeviceRepository
from app.infrastructure.stream.scrcpy import ScrcpyEncoder


# ---------------------------------------------------------------------------
# 单例基础设施实例
# ---------------------------------------------------------------------------
# @lru_cache() 确保每个实现只创建一次。
# 这是安全的，因为：
#   - AdbCliDriver 是无状态的（每次调用生成子进程）
#   - SQLite 仓库每次操作打开新连接（aiosqlite）
#   - ScrcpyEncoder 跟踪单个子进程（在当前每进程单设备模型中没问题）

@lru_cache()
def get_adb_driver() -> AdbDriver:
    """返回进程级 ADB 驱动单例。"""
    return AdbCliDriver()


@lru_cache()
def get_device_repository() -> DeviceRepository:
    """返回进程级设备仓库单例。"""
    return SqliteDeviceRepository()


@lru_cache()
def get_debug_repository() -> DebugRepository:
    """返回进程级调试仓库单例。"""
    return SqliteDebugRepository()


@lru_cache()
def get_video_encoder() -> VideoEncoder:
    """返回进程级视频编码器单例。"""
    return ScrcpyEncoder()


# ---------------------------------------------------------------------------
# 瞬态应用服务工厂
# ---------------------------------------------------------------------------
# 这些由 FastAPI 的 Depends() 在每个请求时调用。
# 它们将单例基础设施注入到应用层用例服务中。

def get_device_service() -> DeviceService:
    """构建一个连接到 ADB 和设备仓库的 DeviceService。"""
    return DeviceService(
        adb=get_adb_driver(),
        repo=get_device_repository(),
    )


def get_debug_service() -> DebugService:
    """构建一个连接到 ADB 和调试仓库的 DebugService。"""
    return DebugService(
        adb=get_adb_driver(),
        repo=get_debug_repository(),
    )


def get_stream_service() -> StreamService:
    """构建一个连接到视频编码器的 StreamService。"""
    return StreamService(encoder=get_video_encoder())


def get_session_service() -> SessionService:
    """构建一个 SessionService（无外部依赖）。"""
    return SessionService()
