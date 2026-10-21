"""
会话管理服务 — 协作
=====================

层：应用层。

管理协作调试会话，多个用户可以同时观察或控制同一台设备。

会话模型：
    - 每个会话有一个 owner（创建者）
    - 参与者有角色："admin" 或 "viewer"
    - 同一时间只有一个用户可以是 "active_controller"
    - 控制权可以从 admin 转移给另一个参与者

当前实现：内存字典（未持久化到数据库）。
这适用于单进程部署。对于多进程或分布式部署，
应使用 Redis 或数据库作为后端。

用例：
    - create_session():   启动新的协作会话
    - join_session():     以给定权限添加参与者
    - leave_session():    移除参与者；如果为空则自动删除
    - transfer_control(): 将控制权转移给另一个用户（仅 admin）
    - get_session():      检索会话信息
"""

import hashlib
from typing import Any, Optional

from app.core.exceptions import (
    InvalidIdentifierError,
    PermissionDeniedError,
    SessionLimitError,
    SessionNotFoundError,
)
from app.core.logging import get_logger
from app.domain.identifiers import is_valid_device_id

logger = get_logger(__name__)

_MAX_COLLAB_SESSIONS = 32  # 活跃协作会话上限（A1 L2：无鉴权下防无界堆积）
_PERMISSIONS = ("admin", "viewer")  # join 角色白名单（A1 L2）


class SessionService:
    """协作会话管理用例。"""

    def __init__(self) -> None:
        # 内存会话存储。键：session_id，值：会话字典。
        # 会话字典结构：
        #   { "id", "device_id", "owner", "participants": {user_id: role}, "active_controller" }
        self.sessions: dict[str, dict[str, Any]] = {}

    async def create_session(self, device_id: str, user_id: str) -> dict[str, Any]:
        """
        创建新的协作会话。

        创建用户同时成为 owner 和 active_controller。
        会话 ID 由 device_id 和 user_id 哈希派生：确定性可重建，但不含
        明文拼接（消「a_b|c 与 a|b_c 同 ID」碰撞面，也不可被枚举猜测，
        A1 L2）。调用方以本方法返回值作为句柄，不自行推导 ID。

        确定性 id 已存在时**幂等返回既有会话**（方案 51 C6）：不覆盖、
        不重置参与者与角色——原实现同参重建会丢弃其他参与者。

        参数：
            device_id: 目标设备的 ADB 序列号。
            user_id: 创建用户的标识。

        返回：
            会话字典，包含 id、device_id、owner、participants、active_controller。

        异常：
            InvalidIdentifierError: device_id 格式非法，或 user_id 为空、
                含 `|` 或控制字符（400，方案 51 C13——收紧派生哈希的入参面）。
            SessionLimitError: 活跃会话数已达上限（503）。
        """
        if not is_valid_device_id(device_id):
            raise InvalidIdentifierError("device_id")
        # user_id 是 sha256 派生 ID 的拼接盐：拒分隔符与控制字符，杜绝拼接碰撞伪装
        if not user_id or "|" in user_id or any(
            ord(ch) < 0x20 or ord(ch) == 0x7F for ch in user_id
        ):
            raise InvalidIdentifierError("user_id")
        session_id = hashlib.sha256(f"{device_id}|{user_id}".encode("utf-8")).hexdigest()[:12]
        existing = self.sessions.get(session_id)
        if existing is not None:
            return existing
        if len(self.sessions) >= _MAX_COLLAB_SESSIONS:
            raise SessionLimitError("协作", _MAX_COLLAB_SESSIONS)
        session = {
            "id": session_id,
            "device_id": device_id,
            "owner": user_id,
            "participants": {user_id: "admin"},
            "active_controller": user_id,
        }
        self.sessions[session_id] = session
        return session

    async def join_session(self, session_id: str, user_id: str, permission: str = "viewer") -> None:
        """
        将参与者添加到现有会话。

        参数：
            session_id: 要加入的会话。
            user_id: 加入的用户。
            permission: 角色 — "admin" 或 "viewer"（默认）。

        异常：
            SessionNotFoundError: 如果 session_id 不存在。
            InvalidIdentifierError: permission 不在白名单（A1 L2，400）。
        """
        if permission not in _PERMISSIONS:
            raise InvalidIdentifierError("permission")
        session = self.sessions.get(session_id)
        if not session:
            raise SessionNotFoundError(session_id)
        session["participants"][user_id] = permission

    async def leave_session(self, session_id: str, user_id: str) -> None:
        """
        从会话中移除参与者。

        如果没有剩余参与者，会话会被自动删除。
        离开者是 active_controller 时控制权按序移交（方案 51 C5）：
        首个剩余 admin → 首个剩余参与者；参与者清空即删会话，
        不存在悬挂的幽灵控制者。协作会话无推送事件通道（调研结论，
        裁决记档方案 51），移交结果记日志，由调用方经 GET 会话接口感知。

        参数：
            session_id: 要离开的会话。
            user_id: 离开的用户。
        """
        session = self.sessions.get(session_id)
        if not session:
            return
        session["participants"].pop(user_id, None)
        if not session["participants"]:
            del self.sessions[session_id]
            return
        if session["active_controller"] == user_id:
            successors = session["participants"]
            new_controller = next(
                (u for u, r in successors.items() if r == "admin"),
                next(iter(successors), None),
            )
            session["active_controller"] = new_controller
            logger.info(
                "controller_changed",
                session=session_id,
                left=user_id,
                new_controller=new_controller,
            )

    async def transfer_control(self, session_id: str, from_user: str, to_user: str) -> None:
        """
        将设备控制权从一个用户转移给另一个。

        只有当前的 admin 可以转移控制权。目标用户必须是会话的参与者。

        参数：
            session_id: 要转移控制的会话。
            from_user: 当前控制者（必须是 admin）。
            to_user: 接收控制权的用户。

        异常：
            SessionNotFoundError: 如果 session_id 不存在。
            PermissionDeniedError: 如果 from_user 不是 admin，
                或 to_user 不是会话参与者（方案 51 C4：原静默假成功）。
        """
        session = self.sessions.get(session_id)
        if not session:
            raise SessionNotFoundError(session_id)
        if session["participants"].get(from_user) != "admin":
            raise PermissionDeniedError("Only admin can transfer control")
        if to_user not in session["participants"]:
            raise PermissionDeniedError("Target user is not a participant")
        session["active_controller"] = to_user

    async def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        """
        检索会话信息。

        参数：
            session_id: 要检索的会话。

        返回：
            找到则返回会话字典，否则返回 None。
        """
        return self.sessions.get(session_id)
