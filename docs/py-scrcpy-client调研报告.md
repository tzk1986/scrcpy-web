# py-scrcpy-client 调研报告

## 调研目的
评估 `py-scrcpy-client` 包是否能够支持 OpenScrcpy 项目后续的使用。

## 包基本信息

- **包名**: `scrcpy-client`
- **版本**: 0.4.7
- **仓库**: https://github.com/leng-yue/py-scrcpy-client
- **许可证**: MIT
- **Python 版本**: >=3.9, <3.13
- **主要依赖**:
  - `av` ^12 - FFmpeg 视频解码库（PyAV）
  - `numpy` ^2 - 数值计算
  - `adbutils` ^2 - ADB 设备管理
  - `PySide6` ^6.0.0 (可选) - GUI 支持

## 核心架构分析

### 1. 连接协议实现

py-scrcpy-client 完整实现了 scrcpy v2.4 协议：

```python
# 1. 建立视频 socket
self.__video_socket = self.device.create_connection(
    Network.LOCAL_ABSTRACT, "scrcpy"
)

# 2. 读取 dummy byte (0x00)
dummy_byte = self.__video_socket.recv(1)

# 3. 建立控制 socket（双连接）
self.control_socket = self.device.create_connection(
    Network.LOCAL_ABSTRACT, "scrcpy"
)

# 4. 读取设备名 (64 字节)
self.device_name = self.__video_socket.recv(64).decode("utf-8").rstrip("\x00")

# 5. 读取分辨率 (4 字节, 大端序)
res = self.__video_socket.recv(4)
self.resolution = struct.unpack(">HH", res)
```

**关键发现**: 我们之前的实现缺少了读取 4 字节分辨率的步骤！

### 2. 服务器启动参数

```python
commands = [
    f"CLASSPATH=/data/local/tmp/{jar_name}",
    "app_process", "/",
    "com.genymobile.scrcpy.Server",
    "2.4",  # scrcpy-server 版本
    "log_level=info",
    f"max_size={self.max_width}",
    f"max_fps={self.max_fps}",
    f"video_bit_rate={self.bitrate}",
    f"video_encoder={self.encoder_name}",  # 或 "OMX.google.h264.encoder"
    f"video_codec={self.codec_name}",       # 或 "h264"
    "tunnel_forward=true",
    "send_frame_meta=false",
    "control=true",
    "audio=false",
    "show_touches=false",
    "stay_awake=false",
    "power_off_on_close=false",
    "clipboard_autosync=false",
]
```

**关键参数**:
- `video_encoder` - 必须指定（默认 OMX.google.h264.encoder）
- `video_codec` - 必须指定（默认 h264）
- `send_frame_meta=false` - 不发送帧元数据
- `control=true` - 启用控制（即使我们不用）

### 3. 视频流处理

```python
def __stream_loop(self) -> None:
    codec = CodecContext.create("h264", "r")  # 使用 PyAV 解码
    while self.alive:
        try:
            raw_h264 = self.__video_socket.recv(0x10000)  # 64KB
            packets = codec.parse(raw_h264)
            for packet in packets:
                frames = codec.decode(packet)
                for frame in frames:
                    frame = frame.to_ndarray(format="bgr24")  # 转为 numpy 数组
                    self.last_frame = frame
                    self.resolution = (frame.shape[1], frame.shape[0])
                    self.__send_to_listeners(EVENT_FRAME, frame)
        except (BlockingIOError, InvalidDataError):
            time.sleep(0.01)
```

**关键特点**:
- 使用 PyAV（FFmpeg 绑定）解码 H.264
- 输出 numpy 数组（BGR24 格式）
- 基于事件监听器模式分发帧

### 4. 控制功能

支持完整的控制协议：
- `keycode(keycode, action, repeat)` - 按键事件
- `text(text)` - 文本输入
- `touch(x, y, action, touch_id)` - 触摸事件
- `scroll(x, y, h, v)` - 滚动事件
- 所有控制通过控制 socket 发送二进制协议

## 与 OpenScrcpy 的适配性分析

### ✅ 优势

1. **协议完整** - 实现了完整的 scrcpy v2.4 协议
2. **控制支持** - 完整的触摸、按键、文本输入支持
3. **成熟稳定** - 经过广泛使用，协议实现正确
4. **代码简洁** - 核心代码仅 ~300 行，易于理解

### ⚠️ 问题与挑战

#### 1. 架构不匹配

**py-scrcpy-client 的设计**:
- 同步阻塞模型（threading）
- 输出 numpy 数组（需要解码）
- 事件监听器模式
- 单设备管理

**OpenScrcpy 的架构要求**:
- 异步模型（asyncio）
- 输出原始 H.264 字节流（WebCodecs 解码）
- WebSocket 推送
- 多设备并发管理
- 四层架构（domain/application/infrastructure/interfaces）

#### 2. 依赖冲突

**py-scrcpy-client 依赖**:
- `av` ^12 - 需要 C 编译器，Windows 构建困难
- `numpy` ^2 - 数值计算库
- `adbutils` ^2 - 另一个 ADB 库

**OpenScrcpy 当前**:
- 使用 `asyncio.create_subprocess_exec` 直接调用 adb
- 不需要 numpy/av
- 轻量级依赖

**引入 py-scrcpy-client 会导致**:
- 需要安装 `av`（Windows 上编译困难）
- 增加 numpy 依赖（但我们只需要原始字节流）
- 两个 ADB 库并存（adbutils vs 我们的 adb CLI）

#### 3. 解码方式不同

**py-scrcpy-client**:
```python
raw_h264 -> PyAV 解码 -> numpy 数组 (BGR24) -> OpenCV 显示
```

**OpenScrcpy**:
```python
raw_h264 -> H264Parser 解析 NALU -> WebSocket -> 浏览器 WebCodecs 解码
```

我们**不需要解码**，只需要解析 NALU 边界并转发给浏览器！

#### 4. 同步 vs 异步

py-scrcpy-client 使用线程：
```python
# 同步阻塞循环
def __stream_loop(self) -> None:
    while self.alive:
        raw_h264 = self.__video_socket.recv(0x10000)
        # ...
```

OpenScrcpy 需要异步：
```python
# 异步循环
async for chunk in stream_service.start_stream(device_id):
    await websocket.send_bytes(chunk)
```

## 集成方案评估

### 方案 A: 直接使用 py-scrcpy-client

**实现方式**:
```python
from scrcpy import Client

async def start_stream(self, device_id: str):
    client = Client(device=device_id, max_width=1080, bitrate=4000000)
    client.start(threaded=True)
    
    # 问题：如何获取原始 H.264？
    # client.last_frame 是解码后的 numpy 数组，不是原始字节流
```

**问题**:
- ❌ 无法获取原始 H.264 字节流（已被解码为 numpy）
- ❌ 需要重新编码为 H.264 才能发给 WebCodecs（性能损失）
- ❌ 同步模型与异步架构冲突
- ❌ 依赖冲突（av, numpy, adbutils）

**结论**: ❌ **不可行**

### 方案 B: 参考 py-scrcpy-client 的协议实现，自己实现异步版本

**实现方式**:
从 py-scrcpy-client 提取协议细节，但保持 OpenScrcpy 的架构：

```python
# 借鉴 py-scrcpy-client 的协议
async def _init_server_connection(self):
    # 1. 建立视频 socket
    self._reader, self._writer = await asyncio.open_connection(...)
    
    # 2. 读取 dummy byte
    dummy = await self._reader.readexactly(1)
    
    # 3. 建立控制 socket
    _, self._control_writer = await asyncio.open_connection(...)
    
    # 4. 读取设备名
    device_name = await self._reader.readexactly(64)
    
    # 5. 读取分辨率（我们之前遗漏了这一步！）
    res = await self._reader.readexactly(4)
    width, height = struct.unpack(">HH", res)
```

**优势**:
- ✅ 保持异步架构
- ✅ 保持四层架构
- ✅ 无需额外依赖
- ✅ 输出原始 H.264（符合 WebCodecs 需求）
- ✅ 参考已验证的协议实现

**实现工作量**:
- 修改 `backend/app/infrastructure/stream/scrcpy.py`
- 补充服务器启动参数（`video_encoder`, `video_codec`）
- 补充协议握手（读取 4 字节分辨率）
- 预计 2-3 小时

**结论**: ✅ **推荐方案**

### 方案 C: 混合使用（部分使用 py-scrcpy-client）

**实现方式**:
- 使用 py-scrcpy-client 的 `ControlSender` 处理控制
- 自己实现视频流部分

**问题**:
- ❌ 仍然有依赖冲突
- ❌ 混合同步/异步模型复杂
- ❌ 架构不一致

**结论**: ❌ **不推荐**

## 详细调研：我们遗漏的协议细节

通过对比 py-scrcpy-client，发现我们之前的实现有以下问题：

### 问题 1: 缺少分辨率读取

**py-scrcpy-client**:
```python
res = self.__video_socket.recv(4)
self.resolution = struct.unpack(">HH", res)
```

**我们的实现**: 跳过了这一步

**影响**: 可能导致协议不同步，服务器认为客户端没有正确初始化

### 问题 2: 缺少必要的服务器参数

**py-scrcpy-client 的参数**:
```python
"video_encoder=OMX.google.h264.encoder",  # 必须指定
"video_codec=h264",                        # 必须指定
"send_frame_meta=false",                   # 必须指定
"control=true",                            # 即使不用也要启用
```

**我们的参数**:
```python
"video=true",
"audio=false",
"control=false",  # ❌ 应该是 true
# ❌ 缺少 video_encoder
# ❌ 缺少 video_codec
# ❌ 缺少 send_frame_meta
```

**影响**: 服务器可能因为缺少必要参数而无法正常启动编码器

### 问题 3: server 版本不匹配

**py-scrcpy-client**: 使用 server v2.4
**我们的实现**: 之前用 v4.1，后来改为 v2.4

**影响**: 不同版本的协议可能有细微差异

## 建议的实施方案

基于调研结果，建议采用 **方案 B**：参考 py-scrcpy-client 的协议实现，自己实现异步版本。

### 具体步骤

1. **修改服务器启动参数**
   ```python
   cmd = [
       "adb", "-s", device_id, "shell",
       f"CLASSPATH={SCRCPY_SERVER_REMOTE_PATH}",
       "app_process", "/",
       SCRCPY_SERVER_CLASS,
       "2.4",  # 使用 v2.4
       "log_level=info",
       f"max_size={opts.max_size}",
       f"max_fps={opts.fps}",
       f"video_bit_rate={bit_rate_value}",
       "video_encoder=OMX.google.h264.encoder",  # 添加
       "video_codec=h264",                        # 添加
       "tunnel_forward=true",
       "send_frame_meta=false",                   # 添加
       "control=true",                            # 改为 true
       "audio=false",
       "show_touches=false",
       "stay_awake=false",
       "power_off_on_close=false",
       "clipboard_autosync=false",
   ]
   ```

2. **补充协议握手**
   ```python
   # 在读取设备名后，读取分辨率
   res = await self._reader.readexactly(4)
   width, height = struct.unpack(">HH", res)
   ```

3. **保持原始 H.264 字节流**
   - 不解码，直接转发
   - 使用 H264Parser 解析 NALU 边界

4. **控制功能**
   - 参考 py-scrcpy-client 的 ControlSender
   - 实现异步版本的控制协议
   - 或直接使用 adb shell input 命令（简单但延迟高）

### 预期效果

- ✅ 视频流正常工作
- ✅ 保持四层架构
- ✅ 异步非阻塞
- ✅ 无额外依赖
- ✅ 输出原始 H.264（WebCodecs 友好）

## 总结

**py-scrcpy-client 的价值**:
- 提供了正确的协议实现参考
- 验证了 scrcpy v2.4 的握手流程
- 展示了控制协议的二进制格式

**是否直接使用**: ❌ **否**
- 架构不匹配（同步 vs 异步）
- 解码方式不同（numpy vs 原始字节流）
- 依赖冲突（av, numpy, adbutils）

**最佳实践**: ✅ **参考其协议实现，自己实现异步版本**
- 保持 OpenScrcpy 的架构优势
- 避免不必要的依赖
- 输出符合 WebCodecs 需求的原始 H.264

**预计工作量**: 2-3 小时
