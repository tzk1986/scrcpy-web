# 前端视频播放器实施记录

## 实施日期
2026-09-08

## 概述

实现前端视频播放器，支持设备屏幕显示和触摸/鼠标输入控制。

## 实现策略

采用**两阶段方案**：
1. **阶段一（当前）**：周期性截屏模式 - 通过 adb exec-out screencap -p 获取设备截屏，定期刷新显示
2. **阶段二（后续）**：WebCodecs H.264 流式传输 - 实现真正的实时视频流

## 文件变更

### 新增文件

#### 1. `frontend/src/services/inputController.ts`
**功能**：输入控制服务
- 坐标映射（浏览器坐标 → 设备坐标）
- 手势识别（点击、滑动、长按）
- 触摸/鼠标事件处理
- 按键和文本输入

**核心类**：
```typescript
class InputController {
  constructor(ws: WebSocketService, deviceWidth: number, deviceHeight: number)
  handleMouseDown(e, canvas)
  handleMouseMove(e, canvas)
  handleMouseUp(e, canvas)
  handleTouchStart(e, canvas)
  handleTouchMove(e, canvas)
  handleTouchEnd(e, canvas)
  sendKey(keycode: number)
  sendText(text: string)
}
```

#### 2. `frontend/src/services/videoStream.ts`
**功能**：视频流服务
- 周期性截屏刷新
- Canvas 渲染（使用 createImageBitmap 优化）
- FPS 统计
- 状态管理

**核心类**：
```typescript
class VideoStream {
  constructor(deviceId: string, canvas: HTMLCanvasElement, ws: WebSocketService, refreshInterval = 1500)
  start(): Promise<void>
  stop(): void
}
```

#### 3. `frontend/src/components/stream/VideoPlayer.vue`
**功能**：视频播放器组件
- 视频画面显示（Canvas）
- 输入事件处理（鼠标/触摸）
- Android 导航键控制
- 状态显示（FPS、帧数、连接状态）
- 错误处理和重连

**布局**：
```
┌─────────────────────────┐
│                         │
│    Canvas（视频画面）     │  ← 触摸/鼠标事件
│                         │
├─────────────────────────┤
│ [返回] [主页] [菜单]     │  ← Android 导航键
│ FPS: 2  帧: 120        │  ← 状态信息
└─────────────────────────┘
```

### 修改文件

#### 1. `backend/app/infrastructure/adb/cli.py`
**优化**：截屏方法使用 `adb exec-out screencap -p` 直接管道输出
- 从 3 次 ADB 调用（shell + pull + rm）优化为 1 次
- 性能提升：从 ~800ms 降至 ~600ms

**变更**：
```python
# 优化前（3步）
await self._run_serial(device_id, "shell", "screencap", "-p", "/sdcard/screenshot.png")
await self._run_serial(device_id, "pull", "/sdcard/screenshot.png", tmp_path)
await self._run_serial(device_id, "shell", "rm", "/sdcard/screenshot.png")

# 优化后（1步）
proc = await asyncio.create_subprocess_exec(
    self.adb_path, "-s", device_id, "exec-out", "screencap", "-p",
    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
)
stdout, stderr = await proc.communicate()
```

#### 2. `frontend/src/services/api.ts`
**新增**：截屏 API 方法
```typescript
async screenshot(deviceId: string): Promise<Blob> {
  const res = await client.get(`/devices/${deviceId}/screenshot`, {
    responseType: 'blob',
  })
  return res.data as Blob
}
```

#### 3. `frontend/src/stores/device.ts`
**新增**：`getDevice` 方法
```typescript
async function getDevice(deviceId: string): Promise<DeviceInfo | null> {
  try {
    return await api.getDevice(deviceId)
  } catch {
    return null
  }
}
```

#### 4. `frontend/src/views/DeviceDetail.vue`
**重构**：使用新的 VideoPlayer 组件
- 移除旧的 canvas 渲染逻辑
- 集成 VideoPlayer 组件
- 自动获取设备分辨率

## 性能测试

### 截屏 API 性能
- **平均耗时**：632 ms
- **最快**：595 ms
- **最慢**：880 ms
- **理论最大 FPS**：1.6
- **建议刷新间隔**：1500ms

### 测试结果
```
单次截屏: 890 ms, 48915 字节
连续截屏（10次）:
  #1: 880 ms
  #2-10: ~600 ms（稳定）
```

## 功能验证

### 截图 API 测试
- HTTP 状态：200
- 返回数据：48KB PNG 图像
- 图像内容：正确显示设备屏幕（Android 9 关于设备页面）

### 前端集成测试
- Vite 开发服务器：正常启动
- 后端服务器：正常启动
- 截屏 API 通过代理：正常
- 设备详情页：正常加载

## 当前限制

1. **帧率低**：~1.5 FPS，不适合实时操作
2. **延迟高**：~600ms 截屏延迟
3. **无真实视频流**：当前为截屏模式

## 后续升级路径

### WebCodecs H.264 流式传输
需要修改后端以输出原始 H.264 NAL 单元：
1. 使用 ffmpeg 从 MP4 提取 H.264
2. 前端使用 WebCodecs VideoDecoder 解码
3. 目标：30+ FPS，<150ms 延迟

### 优化选项
- 使用 scrcpy 直接输出 H.264（需要修改方案A）
- 使用 WebTransport 替代 WebSocket
- 实现自适应码率

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

## 测试脚本

```bash
# 截屏性能测试
python test_screenshot_perf.py

# 单设备 WebSocket 测试
python test_ws_integration.py

# 多设备 WebSocket 测试
python test_multi_device_ws.py
```

## 总结

前端视频播放器基础功能已完成：
- ✅ 周期性截屏显示（~1.5 FPS）
- ✅ 触摸/鼠标输入控制
- ✅ 坐标映射
- ✅ 手势识别（点击、滑动、长按）
- ✅ Android 导航键
- ✅ 设备详情页集成

后续可升级到真正的 H.264 视频流实现更高帧率和更低延迟。
