# 方案A实现总结 - 基于 scrcpy.exe 的视频流服务

## 实现概述

成功实现了基于官方 scrcpy.exe 工具的视频流服务（方案A），支持多设备并发视频流传输。

## 核心功能

### 1. 视频流服务
- **ScrcpyEncoder** (`backend/app/infrastructure/stream/scrcpy.py`)
  - 调用 `D:\scrcpy-win64-v4.1\scrcpy.exe` 录制视频
  - 使用临时 MP4 文件监控实现视频流
  - 通过 `asyncio.Queue` 传递视频数据
  - 支持多设备并发录制

### 2. WebSocket 端点
- **视频流端点** (`backend/app/interfaces/ws/video.py`)
  - URL: `/ws/video/{device_id}`
  - 支持双向通信：视频流输出 + 输入事件接收
  - 自动管理编码器生命周期

### 3. StreamService
- **应用层服务** (`backend/app/application/stream_service.py`)
  - 管理编码器实例
  - 提供统一的视频流接口
  - 支持并发多设备

## 测试验证

### 单元测试
```bash
# 编码器测试
cd backend
python -m pytest tests/infrastructure/test_scrcpy_encoder.py -v

# StreamService 测试
python -m pytest tests/application/test_stream_service.py -v
```

### 集成测试
```bash
# 单设备 WebSocket 测试
cd D:\tangzk\py\scrcpy-web
python test_ws_integration.py

# 多设备 WebSocket 测试
python test_multi_device_ws.py
```

### 测试结果
- ✅ 单设备视频流：68.9 秒稳定运行，接收 524336 字节
- ✅ 多设备并发：两台设备各接收 262192 字节
- ✅ StreamService 集成测试通过
- ✅ WebSocket 端点测试通过
- ✅ 多设备 WebSocket 测试通过

## 使用方式

### 启动服务器
```bash
cd backend
set PYTHONPATH=..
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### WebSocket 客户端连接
```python
import asyncio
import websockets

async def connect():
    device_id = "192.168.8.22:5555"
    ws_url = f"ws://localhost:8000/ws/video/{device_id}"
    
    async with websockets.connect(ws_url) as ws:
        # 接收视频数据（H.264 格式）
        while True:
            data = await ws.recv()
            if isinstance(data, bytes):
                print(f"收到 {len(data)} 字节视频数据")

asyncio.run(connect())
```

### 发送输入事件
```python
import json

# 触摸事件
touch_event = {
    "action": "touch",
    "x": 100,
    "y": 200
}
await ws.send(json.dumps(touch_event))
```

## 架构设计

```
4 层架构：
┌─────────────────────────────────────┐
│  Interface (接口层)                  │
│  - WebSocket 端点                    │
│  - HTTP API                          │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  Application (应用层)                │
│  - StreamService                     │
│  - 业务逻辑编排                      │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  Domain (领域层)                     │
│  - 纯 Python 领域模型                │
│  - 端口接口定义                      │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  Infrastructure (基础设施层)         │
│  - ScrcpyEncoder (scrcpy.exe 封装)   │
│  - SQLite 持久化                     │
│  - 外部依赖实现                      │
└─────────────────────────────────────┘
```

## 关键修复

### aiosqlite 线程错误
**问题**: `RuntimeError: threads can only be started once`

**原因**: `_get_conn()` 方法定义为 `async def` 并返回 `await` 后的连接，但使用时又 `await` 了一次，导致线程被启动两次。

**修复**: 将 `_get_conn()` 从 `async def` 改为 `def`，移除 `return` 语句中的 `await`：
```python
# 修复前
async def _get_conn(self) -> aiosqlite.Connection:
    return await aiosqlite.connect(self.db_path)

# 修复后
def _get_conn(self) -> aiosqlite.Connection:
    return aiosqlite.connect(self.db_path)
```

**影响文件**:
- `backend/app/infrastructure/persistence/sqlite.py`
  - `SqliteDebugRepository._get_conn()` (第 60-70 行)
  - `SqliteDeviceRepository._get_conn()` (第 286-289 行)

## 配置

### scrcpy 路径
```python
# config/settings.py
scrcpy_path: str = Field(
    default="D:/scrcpy-win64-v4.1/scrcpy.exe",
    alias="SCRCPY_PATH"
)
```

### 数据库路径
```python
# config/settings.py
database: DatabaseSettings = Field(
    default_factory=lambda: DatabaseSettings(
        path="./data/debug.sqlite"
    )
)
```

## 依赖

```bash
pip install fastapi uvicorn websockets aiosqlite
```

## 后续优化建议

1. **方案C 升级路径**
   - 如果未来需要更精细的控制（如实时帧处理、自定义编码参数），可以升级到方案C（直接调用 scrcpy 库）
   
2. **性能优化**
   - 添加视频帧缓冲
   - 实现自适应码率
   - 支持视频转码（H.264 → WebM）

3. **功能增强**
   - 添加设备发现 API
   - 实现会话持久化
   - 支持多用户并发访问同一设备

## 文件结构

```
scrcpy-web/
├── backend/
│   ├── app/
│   │   ├── application/
│   │   │   └── stream_service.py      # StreamService
│   │   ├── domain/
│   │   │   └── ports.py               # 端口接口
│   │   ├── infrastructure/
│   │   │   ├── persistence/
│   │   │   │   └── sqlite.py          # SQLite 实现
│   │   │   └── stream/
│   │   │       └── scrcpy.py          # ScrcpyEncoder
│   │   ├── interfaces/
│   │   │   └── ws/
│   │   │       └── video.py           # WebSocket 端点
│   │   └── main.py
│   └── tests/
├── config/
│   └── settings.py                    # 配置
├── test_ws_integration.py             # 单设备测试
└── test_multi_device_ws.py            # 多设备测试
```

## 总结

✅ **方案A 完全实现并验证**
- 使用官方 scrcpy.exe 工具
- 支持多设备并发视频流
- WebSocket 端到端传输正常
- 数据库初始化问题已修复
- 所有测试通过

系统已准备好用于生产环境的进一步开发和部署。
