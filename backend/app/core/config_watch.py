"""
配置文件热重载监听
===================

层：横切关注点（启动引导）。

按固定间隔轮询 config/*.yaml 的 mtime，检测到变化时重新加载配置
（`config.settings.reload_settings`），让运行时读取 `settings()` 的
消费点无需重启即可拿到新值。

范围与边界（方案 01「配置热重载」）：
    - 监听对象：config/base.yaml 与 config/{APP_ENV}.yaml
    - .env 不监听：load_dotenv(override=False) 语义下已存在的环境变量
      不会被 .env 修改覆盖，改 .env 需重启生效
    - server.host/port 变更需重启（uvicorn 已绑定旧地址），仅记录告警
    - 数据库路径、连接池大小等构造时读取的配置不热切换
    - 重载失败（YAML 语法错误等）保留旧配置继续运行，仅记录告警

零额外依赖：asyncio 轮询 mtime，不引入 watchdog。
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import config.settings as config_settings
from app.core.logging import get_logger
from config.settings import reload_settings, settings

logger = get_logger(__name__)

CONFIG_DIR = Path(config_settings.__file__).parent


def watched_paths() -> list[Path]:
    """当前应监听的配置文件清单：base.yaml + 当前 APP_ENV 的环境文件。"""
    paths = [CONFIG_DIR / "base.yaml"]
    env = os.getenv("APP_ENV", "base")
    if env != "base":
        env_file = CONFIG_DIR / f"{env}.yaml"
        if env_file.exists():
            paths.append(env_file)
    return paths


def reload_config() -> dict[str, Any]:
    """
    执行一次配置热重载（监听循环与手动触发端点共用）。

    返回：
        {"reloaded": True, "restart_required": bool}，或
        {"reloaded": False, "error": str}（重载失败，旧配置保持生效）。
    """
    old = settings()
    try:
        new = reload_settings()
    except Exception as e:
        logger.warning("config_reload_failed_keep_old", error=str(e))
        return {"reloaded": False, "error": str(e)}

    bind_changed = (old.server.host, old.server.port) != (new.server.host, new.server.port)
    if bind_changed:
        logger.warning(
            "config_reload_server_bind_changed_restart_required",
            old=f"{old.server.host}:{old.server.port}",
            new=f"{new.server.host}:{new.server.port}",
        )
    logger.info("config_reloaded", restart_required=bind_changed)
    return {"reloaded": True, "restart_required": bind_changed}


class ConfigWatcher:
    """按间隔轮询配置文件 mtime，变化时热重载。"""

    def __init__(self, interval: float = 2.0) -> None:
        self.interval = interval
        self._task: asyncio.Task[None] | None = None
        self._mtimes: dict[Path, float] = {}

    @staticmethod
    def scan() -> dict[Path, float]:
        """扫描一次被监听文件的 mtime 快照（缺失文件记为 0.0）。"""
        snapshot: dict[Path, float] = {}
        for path in watched_paths():
            try:
                snapshot[path] = path.stat().st_mtime
            except OSError:
                snapshot[path] = 0.0
        return snapshot

    def poll_changed(self) -> bool:
        """检查 mtime 是否变化；变化则更新快照并返回 True。"""
        current = self.scan()
        if current != self._mtimes:
            self._mtimes = current
            return True
        return False

    async def run(self) -> None:
        """监听循环：启动时先建快照，之后每 interval 秒检查一次。"""
        self._mtimes = self.scan()
        while True:
            await asyncio.sleep(self.interval)
            if self.poll_changed():
                reload_config()

    def start(self) -> None:
        """启动监听后台任务（幂等）。"""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self.run())
        logger.info("config_watcher_started", interval=self.interval)

    async def stop(self) -> None:
        """停止监听后台任务（幂等）。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("config_watcher_stopped")