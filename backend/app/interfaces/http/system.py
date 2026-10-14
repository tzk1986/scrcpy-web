"""
系统配置 HTTP 端点
====================

层：接口 → HTTP。

提供配置热重载的手动触发入口（后台监听任务之外的补充）：

    POST /api/system/config/reload — 重新加载 config/*.yaml 并替换配置缓存

用于监听关闭（app.config_watch=false）或需要立即生效（不等轮询间隔）
的场景。重载失败时返回 400 CONFIG_RELOAD_FAILED，旧配置保持生效。

另有（方案 45 §4.1-③/§4.2）：

    GET /api/system/runtime-env — 运行环境信息（只读透明化）
    GET /api/system/scrcpy-update — scrcpy-server 上游版本检查（仅手动触发提示）
"""

import asyncio
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter

from app.core.config_watch import reload_config
from app.core.exceptions import ConfigReloadError
from app.core.launcher import request_shutdown
from app.core.platform import CREATE_NO_WINDOW
from app.scrcpy.constants import SCRCPY_SERVER_VERSION
from app.scrcpy.server_manager import ServerManager
from config.settings import settings

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


@router.post("/shutdown")
async def shutdown_endpoint() -> dict[str, Any]:
    """
    请求优雅停机（方案 23 T2）。

    停机动作经启动器注册表触发（run_server 注册的回调仅置 uvicorn
    Server.should_exit 标志——当前响应完整送达后才停止，浏览器可先拿到
    accepted 再进入退出指引）。

    docker 直跑 uvicorn（无注册回调）时返回 accepted=false 不假成功。

    返回：
        {"accepted": true, "message": "服务正在退出"}  已接入在线退出
        {"accepted": false, "message": "..."}          未接入，需手动停止
    """
    accepted = request_shutdown()
    return {
        "accepted": accepted,
        "message": "服务正在退出" if accepted else "当前运行方式未接入在线退出，请手动停止服务",
    }


def _probe_adb_version(adb_path: str) -> str:
    """adb --version 探测（阻塞子进程，端点内经 asyncio.to_thread 执行）；失败返回「未知」。"""
    try:
        result = subprocess.run(
            [adb_path, "version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in result.stdout.splitlines():
            if "Version" in line:
                return line.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "未知"


@router.get("/runtime-env")
async def runtime_env_endpoint() -> dict[str, Any]:
    """运行环境信息（方案 45 §4.1-③）：只读透明化，不改动二进制获取方式。"""
    # 与全仓一致走 .path 自动检测（源码 tools/adb.exe、frozen _internal/tools/、回退 "adb"）
    adb_path = settings().adb.path
    adb_version = await asyncio.to_thread(_probe_adb_version, adb_path)
    return {
        "scrcpy_server_version": SCRCPY_SERVER_VERSION,
        "jar_path": str(ServerManager().get_local_jar_path()),
        "adb_path": adb_path,
        "adb_version": adb_version,
        "frozen": bool(getattr(sys, "frozen", False)),
    }


_GITHUB_RELEASES_LATEST = "https://api.github.com/repos/Genymobile/scrcpy/releases/latest"
_UPDATE_USER_AGENT = "OpenScrcpy-check-update"


def _parse_version(text: str) -> tuple[int, int, int] | None:
    """解析 vX.Y.Z / X.Y / X 为固定三元组（缺省段补 0）；非版本串返回 None。"""
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def _fetch_latest_release() -> dict[str, Any]:
    """GitHub Releases latest 查询（阻塞网络调用，仅在 asyncio.to_thread 内执行）。"""
    request = urllib.request.Request(
        _GITHUB_RELEASES_LATEST,
        headers={
            "User-Agent": _UPDATE_USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub API 返回非对象")
    return payload


@router.get("/scrcpy-update")
async def scrcpy_update_endpoint() -> dict[str, Any]:
    """检查上游 scrcpy-server 版本（方案 45 §4.2：仅手动触发提示，不自动下载）。

    不联网轮询；离线/内网环境报明确 unknown 原因。升级仍走方案 44 换包 SOP。
    """
    current = SCRCPY_SERVER_VERSION
    cur = _parse_version(current)
    try:
        release = await asyncio.to_thread(_fetch_latest_release)
    except urllib.error.URLError as e:
        return {
            "status": "unknown", "current_version": current, "latest_version": None,
            "url": None, "error": f"无法访问 GitHub（离线/内网环境？）：{e.reason}",
        }
    except Exception as e:
        return {
            "status": "unknown", "current_version": current, "latest_version": None,
            "url": None, "error": f"检查更新失败：{e}",
        }
    latest_tag = str(release.get("tag_name") or "")
    url = release.get("html_url")
    latest = _parse_version(latest_tag)
    if latest is None:
        return {
            "status": "unknown", "current_version": current,
            "latest_version": latest_tag or None, "url": url,
            "error": f"无法解析上游版本号：{latest_tag or '(空)'}",
        }
    if cur is None:
        return {
            "status": "unknown", "current_version": current, "latest_version": latest_tag,
            "url": url, "error": f"无法解析本地版本号：{current}",
        }
    if latest > cur:
        status = "updateAvailable"
    elif latest == cur:
        status = "upToDate"
    else:
        status = "installedNewer"
    return {
        "status": status, "current_version": current,
        "latest_version": latest_tag, "url": url, "error": None,
    }