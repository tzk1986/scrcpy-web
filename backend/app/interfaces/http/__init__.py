"""
HTTP 端点
==========

接口层的 HTTP 子包。

每个模块定义一个 FastAPI APIRouter，包含一组相关的 RESTful 端点。
路由在 main.py 中通过 app.include_router() 注册。

端点设计原则：
    - 使用 Depends() 注入应用层服务
    - 返回 dict 或 Response（FastAPI 自动序列化为 JSON）
    - 错误通过统一的异常处理器处理（见 core/exceptions.py）
    - 参数通过路径参数、查询参数、请求体传递
"""
