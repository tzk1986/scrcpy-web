"""
应用管理 HTTP 端点
===================

层：接口层。

提供设备应用管理的 RESTful API：
- GET /api/apps/{device_id}                    应用列表
- GET /api/apps/{device_id}/{package}          应用详情
- POST /api/apps/{device_id}/{package}/launch  启动应用
- POST /api/apps/{device_id}/{package}/stop    强制停止
- POST /api/apps/{device_id}/{package}/uninstall  卸载
- POST /api/apps/{device_id}/{package}/clear-data  清除数据
- GET /api/apps/{device_id}/{package}/memory   内存占用

参考方案文档：方案/14-调试面板其他标签完善.md
"""

from fastapi import APIRouter, Depends, HTTPException

from app.application.app_service import AppService
from app.deps import get_app_service
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/apps", tags=["apps"])


def _app_to_dict(app) -> dict:
    """将 AppInfo 转为字典。"""
    return {
        "package_name": app.package_name,
        "version_name": app.version_name,
        "version_code": app.version_code,
        "install_time": app.install_time,
        "update_time": app.update_time,
        "apk_size_mb": app.apk_size_mb,
        "is_system": app.is_system,
        "is_running": app.is_running,
        "pid": app.pid,
        "memory_kb": app.memory_kb,
    }


@router.get("/{device_id}")
async def list_apps(
    device_id: str,
    include_system: bool = False,
    service: AppService = Depends(get_app_service),
):
    """
    获取设备应用列表。

    参数：
        device_id: 设备 ID。
        include_system: 是否包含系统应用（默认仅第三方）。
        service: 应用管理服务。

    返回：
        JSON 对象，包含 apps 列表和统计信息。
    """
    apps = await service.list_apps(device_id, include_system)

    running_count = sum(1 for app in apps if app.is_running)
    total_memory_kb = sum(app.memory_kb or 0 for app in apps)

    return {
        "apps": [_app_to_dict(app) for app in apps],
        "total": len(apps),
        "running_count": running_count,
        "total_memory_mb": round(total_memory_kb / 1024, 1),
    }


@router.get("/{device_id}/{package}")
async def get_app_info(
    device_id: str,
    package: str,
    service: AppService = Depends(get_app_service),
):
    """
    获取应用详情。

    参数：
        device_id: 设备 ID。
        package: 应用包名。
        service: 应用管理服务。

    返回：
        JSON 对象，包含应用详情。
    """
    app = await service.get_app_info(device_id, package)
    if not app:
        raise HTTPException(status_code=404, detail=f"App not found: {package}")

    return _app_to_dict(app)


@router.post("/{device_id}/{package}/launch")
async def launch_app(
    device_id: str,
    package: str,
    service: AppService = Depends(get_app_service),
):
    """
    启动应用。

    参数：
        device_id: 设备 ID。
        package: 应用包名。
        service: 应用管理服务。

    返回：
        JSON 对象，包含成功状态。
    """
    success = await service.launch_app(device_id, package)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to launch app: {package}")

    return {"success": True, "package": package}


@router.post("/{device_id}/{package}/stop")
async def stop_app(
    device_id: str,
    package: str,
    service: AppService = Depends(get_app_service),
):
    """
    强制停止应用。

    参数：
        device_id: 设备 ID。
        package: 应用包名。
        service: 应用管理服务。

    返回：
        JSON 对象，包含成功状态。
    """
    success = await service.stop_app(device_id, package)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to stop app: {package}")

    return {"success": True, "package": package}


@router.post("/{device_id}/{package}/uninstall")
async def uninstall_app(
    device_id: str,
    package: str,
    service: AppService = Depends(get_app_service),
):
    """
    卸载应用。

    参数：
        device_id: 设备 ID。
        package: 应用包名。
        service: 应用管理服务。

    返回：
        JSON 对象，包含成功状态。
    """
    success = await service.uninstall_app(device_id, package)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to uninstall app: {package}")

    return {"success": True, "package": package}


@router.post("/{device_id}/{package}/clear-data")
async def clear_app_data(
    device_id: str,
    package: str,
    service: AppService = Depends(get_app_service),
):
    """
    清除应用数据。

    参数：
        device_id: 设备 ID。
        package: 应用包名。
        service: 应用管理服务。

    返回：
        JSON 对象，包含成功状态。
    """
    success = await service.clear_app_data(device_id, package)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to clear app data: {package}")

    return {"success": True, "package": package}


@router.get("/{device_id}/{package}/memory")
async def get_app_memory(
    device_id: str,
    package: str,
    service: AppService = Depends(get_app_service),
):
    """
    获取应用内存占用。

    参数：
        device_id: 设备 ID。
        package: 应用包名。
        service: 应用管理服务。

    返回：
        JSON 对象，包含内存占用（KB）。
    """
    memory_kb = await service.get_app_memory(device_id, package)
    if memory_kb is None:
        raise HTTPException(status_code=404, detail=f"Memory info not available for: {package}")

    return {"package": package, "memory_kb": memory_kb}
