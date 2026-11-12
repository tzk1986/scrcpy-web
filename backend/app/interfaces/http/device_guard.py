"""HTTP 入站 device_id 统一校验（方案 58 C-16，8.7 入站标识校验接线）。

is_valid_device_id 自方案 37 A5 起仅有谓词、HTTP 路径参数从未接线：
垃圾 device_id 可直通 adb argv（ConPTY 经 cmd /C 拼接）、DB 查询与
服务层缓冲字典键。本守卫以**路由级依赖**注入六个含 /{device_id} 路径
参数的路由（devices/apps/files/network/performance/recording），避免
逐一改动约 37 个端点签名：

    - 读取 path_params 中的 device_id，缺失（字面路由如 /events、
      /connect、/batch/install）→ 放行，由端点自身校验继续负责；
    - 命中且格式非法 → InvalidIdentifierError（400，不回显原值）。
"""
from fastapi import Request

from app.core.exceptions import InvalidIdentifierError
from app.domain.identifiers import is_valid_device_id


def require_valid_device_id(request: Request) -> None:
    """路由级依赖：校验路径参数 device_id（见模块 docstring）。"""
    device_id = request.path_params.get("device_id")
    if device_id is not None and not is_valid_device_id(device_id):
        raise InvalidIdentifierError("device_id")
