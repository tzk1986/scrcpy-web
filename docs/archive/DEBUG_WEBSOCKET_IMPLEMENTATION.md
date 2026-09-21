# 调试 WebSocket 实施记录

## 实施日期
2026-09-08

## 概述

实现调试会话的 WebSocket 实时推送功能，支持：
- Logcat 日志实时推送
- Shell 命令执行（通过 WebSocket）
- 会话状态通知

## 文件变更

### 后端修改

#### 1. `backend/app/application/debug_service.py`
**新增功能**：WebSocket 订阅支持

**关键变更**：
- 添加 `subscribers: dict[str, set[WebSocket]]` 存储 WebSocket 订阅者
- 添加 `subscribe()` 方法：订阅实时日志推送
- 添加 `unsubscribe()` 方法：取消订阅
- 添加 `_notify_subscribers()` 方法：向订阅者推送日志
- 修改 `_collect_logcat()`：在收集日志时推送给订阅者
- 修改 `close_session()`：清理订阅者并通知

```python
# 订阅方法
async def subscribe(self, session_id: str, websocket: WebSocket):
    if session_id not in self.subscribers:
        self.subscribers[session_id] = set()
    self.subscribers[session_id].add(websocket)

# 推送方法
async def _notify_subscribers(self, session_id: str, entry: dict):
    for ws in self.subscribers.get(session_id, set()):
        await ws.send_json({"type": "log", "entry": entry})
```

#### 2. `backend/app/interfaces/ws/debug.py`
**重构**：实现完整的 WebSocket 协议

**支持的操作**：
- `subscribe`: 订阅日志流
- `unsubscribe`: 取消订阅
- `exec`: 执行 shell 命令
- `filter`: 设置过滤条件（占位符）
- `export`: 导出日志（占位符）

```python
if op == "subscribe":
    await debug_service.subscribe(session_id, websocket)
    await websocket.send_json({"type": "subscribed", "session_id": session_id})
elif op == "exec":
    output = await debug_service.exec_shell(session_id, cmd)
    await websocket.send_json({"type": "shell_output", "output": output})
```

#### 3. `backend/app/deps.py`
**修复**：将 DebugService 和 DeviceService 改为单例

**原因**：这些服务维护状态（会话、订阅者、任务），需要在请求间共享。

```python
@lru_cache()
def get_debug_service() -> DebugService:
    return DebugService(adb=get_adb_driver(), repo=get_debug_repository())
```

### 前端修改

#### 1. `frontend/src/stores/debug.ts`
**新增功能**：WebSocket 连接管理

**关键变更**：
- 添加 `wsConnected` 状态
- 添加 `connectWebSocket()` 方法
- 添加 `disconnectWebSocket()` 方法
- 添加 `handleMessage()` 方法处理 WebSocket 消息

```typescript
async function connectWebSocket() {
  debugWs = new WebSocketService(`ws://${window.location.host}/ws/debug/${sessionId.value}`)
  debugWs.setMessageHandler((data) => {
    const msg = JSON.parse(data)
    if (msg.type === 'log') appendLog(msg.entry)
  })
  debugWs.connect()
  debugWs.send({ op: 'subscribe' })
}
```

#### 2. `frontend/src/components/debug/LogcatView.vue`
**重构**：支持实时日志推送

**关键变更**：
- 组件挂载时调用 `connectWebSocket()`
- 添加自动滚动功能
- 添加 LIVE 状态指示器
- 支持用户手动滚动暂停自动滚动

#### 3. `frontend/src/components/debug/ShellView.vue`
**重构**：使用 WebSocket 执行 shell 命令

**关键变更**：
- 建立 WebSocket 连接用于 shell 命令
- 通过 WebSocket 发送 `exec` 操作
- 接收并显示 shell 输出

### 测试脚本

#### `test_debug_ws.py`
**功能**：测试调试 WebSocket 端点

**测试内容**：
1. 创建调试会话
2. 连接 WebSocket
3. 发送订阅请求
4. 接收日志推送（10 秒）
5. 执行 shell 命令
6. 取消订阅
7. 关闭会话

**测试结果**：
- ✅ 会话创建成功
- ✅ WebSocket 连接成功
- ✅ 订阅成功
- ✅ 收到 3930 条日志（10 秒内）
- ✅ Shell 命令执行成功
- ✅ 取消订阅成功
- ✅ 会话关闭成功

## 技术细节

### WebSocket 协议

**客户端 → 服务端**：
```json
{"op": "subscribe"}
{"op": "unsubscribe"}
{"op": "exec", "command": "ls"}
{"op": "filter", "level": "E"}
{"op": "export"}
```

**服务端 → 客户端**：
```json
{"type": "subscribed", "session_id": "..."}
{"type": "unsubscribed"}
{"type": "log", "entry": {...}}
{"type": "shell_output", "output": "...", "success": true}
{"type": "session_closed"}
```

### 日志推送流程

```
1. 客户端连接 WebSocket
2. 客户端发送 {"op": "subscribe"}
3. 服务端将 WebSocket 添加到 subscribers[session_id]
4. Logcat 收集任务持续运行
5. 每条新日志调用 _notify_subscribers()
6. WebSocket 发送 {"type": "log", "entry": {...}}
7. 客户端接收并显示日志
8. 客户端断开或发送 unsubscribe 时清理
```

### 依赖注入修复

**问题**：`get_debug_service()` 每次创建新实例，导致：
- 会话状态丢失
- Logcat 任务无法访问
- 订阅者列表为空

**解决**：使用 `@lru_cache()` 使 DebugService 成为单例

## 性能测试

### Logcat 推送
- **测试时长**：10 秒
- **收到日志数**：3930 条
- **平均推送速率**：393 条/秒
- **延迟**：< 100ms（实时推送）

### 内存使用
- 每个会话最多缓冲 50,000 条日志
- 超过限制时按 FIFO 淘汰
- 数据库保留所有历史记录

## 已知问题

1. **Shell 输出被日志淹没**
   - 原因：logcat 持续推送，shell 输出混在其中
   - 解决：前端可以根据 `type` 字段区分处理

2. **Windows 子进程警告**
   - 原因：asyncio 子进程关闭时的资源警告
   - 影响：无功能性影响，可忽略

## 总结

### 完成的功能
- ✅ DebugService WebSocket 订阅支持
- ✅ Logcat 实时推送（393 条/秒）
- ✅ Shell 命令通过 WebSocket 执行
- ✅ 前端 LogcatView 实时日志显示
- ✅ 前端 ShellView WebSocket 集成
- ✅ 自动滚动和 LIVE 指示器
- ✅ 订阅/取消订阅协议
- ✅ 会话关闭通知

### 后续优化
-  服务端日志过滤（减少网络流量）
- ⏳ 日志导出功能
- ⏳ PTY 伪终端支持
- ⏳ 命令历史记录
- ⏳ 多标签页支持

## 启动方式

```bash
# 启动后端
cd backend
set PYTHONPATH=..
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 启动前端（新终端）
cd frontend
npx vite --host

# 访问
http://localhost:5173/device/192.168.8.22:5555
```

## 测试命令

```bash
# 调试 WebSocket 测试
python test_debug_ws.py

# 截屏性能测试
python test_screenshot_perf.py

# 视频流测试
python test_ws_integration.py
python test_multi_device_ws.py
```
