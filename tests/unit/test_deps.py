"""deps.py 依赖注入单例语义测试（方案 34 D8）。

背景：get_stream_service / get_session_service 曾缺 @lru_cache()，
与同文件其他「进程级单例」（'单例服务使用 @lru_cache()' 约定）不一致。
每 WS/HTTP 请求经 Depends(...) 新建实例导致跨会话状态各自独立：

- StreamService.active_streams 独立 → D4 acquire 守卫永不拒绝（各自空
  dict）、多会话并发各起 scrcpy-server（设备端 SIGABRT 互踩，2026-10-08
  .33 验收现场日志实证 returncode=134）；
- SessionService._sessions 独立 → 协作会话创建后查询不可见（create 存进
  实例 A，join/get 落在实例 B 的另一只空 dict）。

钉子：注入函数必须返回进程级同一实例，跨会话状态（active_streams /
_sessions）必须是同一对象。
"""

from app.deps import get_session_service, get_stream_service


def test_get_stream_service_returns_same_instance():
    assert get_stream_service() is get_stream_service()


def test_get_session_service_returns_same_instance():
    assert get_session_service() is get_session_service()


def test_stream_service_shares_active_streams_dict():
    """同一实例的 active_streams 必须是同一 dict——acquire 守卫可见性前提。"""
    svc_a = get_stream_service()
    svc_b = get_stream_service()
    assert svc_a.active_streams is svc_b.active_streams


def test_session_service_shares_sessions_dict():
    """协作会话注册表必须是同一 dict——create 后 join/get 可见的前提。"""
    svc_a = get_session_service()
    svc_b = get_session_service()
    assert svc_a.sessions is svc_b.sessions