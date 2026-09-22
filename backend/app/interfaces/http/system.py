"""
系统配置 HTTP 端点
====================

层：接口 → HTTP。

提供配置热重载的手动触发入口（后台监听任务之外的补充）：

    POST /api/system/config/reload — 重新加载 config/*.yaml 并替换配置缓存

用于监听关闭（app.config_watch=false）或需要立即生效（不等轮询间隔）
的场景。重载失败时返回 400 CONFIG_RELOAD_FAILED，旧配置保持生效。
"""

from typing import Any

from fastapi import APIRouter

from app.core.config_watch import reload_config
from app.core.exceptions import ConfigReloadError

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/config/reload")
async def reload_config_endpoint() -> dict[str, Any]:
    """
    手动触发配置热重载。

    返回：
        {"reloaded": true, "restart_required": bool}

        restart_required 为 true 表示监听生效的 server.host/port 发生变化，
        需重启服务才能绑定新地址。

    异常：
        ConfigReloadError: 配置文件解析失败（400），旧配置保持生效。
    """
    result = reload_config()
    if not result["reloaded"]:
        raise ConfigReloadError(result["error"])
    return result