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

from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class SessionService:
    """协作会话管理用例。"""

    def __init__(self):
        # 内存会话存储。键：session_id，值：会话字典。
        # 会话字典结构：
        #   { "id", "device_id", "owner", "participants": {user_id: role}, "active_controller" }
        self.sessions: dict[str, dict] = {}

    async def create_session(self, device_id: str, user_id: str) -> dict:
        """
        创建新的协作会话。

        创建用户同时成为 owner 和 active_controller。
        会话 ID 由 device_id 和 user_id 派生以保证确定性。

        参数：
            device_id: 目标设备的 ADB 序列号。
            user_id: 创建用户的标识。

        返回：
            会话字典，包含 id、device_id、owner、participants、active_controller。
        """
        session_id = f"{device_id}_{user_id}"
        session = {
            "id": session_id,
            "device_id": device_id,
            "owner": user_id,
            "participants": {user_id: "admin"},
            "active_controller": user_id,
        }
        self.sessions[session_id] = session
        return session

    async def join_session(self, session_id: str, user_id: str, permission: str = "viewer"):
        """
        将参与者添加到现有会话。

        参数：
            session_id: 要加入的会话。
            user_id: 加入的用户。
            permission: 角色 — "admin" 或 "viewer"（默认）。

        异常：
            ValueError: 如果 session_id 不存在。
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError("Session not found")
        session["participants"][user_id] = permission

    async def leave_session(self, session_id: str, user_id: str):
        """
        从会话中移除参与者。

        如果没有剩余参与者，会话会被自动删除。

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

    async def transfer_control(self, session_id: str, from_user: str, to_user: str):
        """
        将设备控制权从一个用户转移给另一个。

        只有当前的 admin 可以转移控制权。目标用户必须是会话的参与者。

        参数：
            session_id: 要转移控制的会话。
            from_user: 当前控制者（必须是 admin）。
            to_user: 接收控制权的用户。

        异常：
            ValueError: 如果 session_id 不存在。
            PermissionError: 如果 from_user 不是 admin。
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError("Session not found")
        if session["participants"].get(from_user) != "admin":
            raise PermissionError("Only admin can transfer control")
        session["active_controller"] = to_user

    async def get_session(self, session_id: str) -> Optional[dict]:
        """
        检索会话信息。

        参数：
            session_id: 要检索的会话。

        返回：
            找到则返回会话字典，否则返回 None。
        """
        return self.sessions.get(session_id)
