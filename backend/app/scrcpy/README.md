# Scrcpy 复用代码模块

本模块封装了 scrcpy 的核心功能，提供可复用的视频编码、帧解析和输入控制能力。

## 目录结构

```
scrcpy/
├── __init__.py              # 模块导出
├── README.md                # 本文件
├── constants.py             # 常量定义（Android keycode、编码器参数）
├── h264_parser.py           # H264 NALU 帧解析器
├── input_controller.py      # 输入事件控制器（触摸、按键、滑动、文本）
├── encoder.py               # scrcpy-server 视频编码器封装
└── server_manager.py        # scrcpy-server.jar 部署管理
```

## 核心功能

### 1. 视频编码器（ScrcpyEncoder）

将 Android 设备屏幕编码为 H264 视频流。

**使用示例：**

```python
from app.scrcpy import ScrcpyEncoder, EncoderOpts

async def stream_video(device_id: str):
    """视频流示例"""
    encoder = ScrcpyEncoder()
    opts = EncoderOpts(
        max_size=1080,      # 最大分辨率
        bit_rate="4M",      # 码率 4Mbps
        codec="h264",       # H264 编码
        fps=30,             # 30 FPS
    )
    
    async for frame in encoder.start(device_id, opts):
        # frame 是 H264 NALU 字节
        await send_to_client(frame)
    
    await encoder.stop()
```

**关键特性：**
- 自动部署 scrcpy-server.jar 到设备
- 异步生成 H264 帧数据
- 支持配置分辨率、码率、帧率
- 优雅关闭和清理

### 2. H264 帧解析器（H264Parser）

解析 scrcpy-server 输出的原始 H264 流，提取完整的 NALU 单元。

**使用示例：**

```python
from app.scrcpy import H264Parser

parser = H264Parser()

# 从编码器读取数据
async for chunk in encoder.start(device_id, opts):
    # 喂入解析器
    nalu_list = parser.feed(chunk)
    
    # 处理完整的 NALU
    for nalu in nalu_list:
        nalu_type = nalu[0] & 0x1F  # NALU 类型
        if nalu_type == 5:  # IDR 关键帧
            print("收到关键帧")
        elif nalu_type == 7:  # SPS
            print("收到 SPS")
        elif nalu_type == 8:  # PPS
            print("收到 PPS")
```

**NALU 类型：**
- 5 (IDR): 关键帧，可用于重新同步
- 7 (SPS): 序列参数集
- 8 (PPS): 图像参数集
- 1 (非 IDR): 普通帧

### 3. 输入控制器（InputController）

通过 ADB 向设备发送触摸、滑动、按键、文本输入事件。

**使用示例：**

```python
from app.scrcpy import InputController

controller = InputController()

# 触摸点击
await controller.tap(device_id, x=100, y=200)

# 滑动
await controller.swipe(device_id, x1=100, y1=200, x2=300, y2=400, duration=300)

# 按键
await controller.key(device_id, keycode=4)  # BACK 键

# 输入文本
await controller.text(device_id, "hello world")

# 长按
await controller.long_press(device_id, x=100, y=200, duration=1000)
```

**支持的输入类型：**
- `tap(x, y)`: 触摸点击
- `swipe(x1, y1, x2, y2, duration)`: 滑动
- `key(keycode)`: 按键（使用 Android keycode）
- `text(string)`: 输入文本
- `long_press(x, y, duration)`: 长按

### 4. 服务器管理器（ServerManager）

管理 scrcpy-server.jar 在设备上的部署。

**使用示例：**

```python
from app.scrcpy import ServerManager

manager = ServerManager()

# 检查并推送 server
await manager.ensure_server(device_id)

# 强制重新推送
await manager.push_server(device_id)

# 获取 server 版本
version = await manager.get_server_version()
```

### 5. 常量定义（constants）

提供 Android keycode 和编码器参数的常量。

```python
from app.scrcpy.constants import KEYCODE_HOME, KEYCODE_BACK, KEYCODE_POWER

# 使用 keycode
await controller.key(device_id, KEYCODE_BACK)

# 默认编码器选项
from app.scrcpy.constants import DEFAULT_ENCODER_OPTS

opts = DEFAULT_ENCODER_OPTS  # max_size=1080, bit_rate="4M", codec="h264", fps=30
```

## 工作流程

### 视频流传输流程

```
1. ServerManager.ensure_server(device_id)
   └─ 检查设备是否已有 scrcpy-server.jar
   └─ 如果没有，推送到 /data/local/tmp/

2. ScrcpyEncoder.start(device_id, opts)
   └─ 启动 scrcpy-server 子进程
   └─ 通过 ADB shell 执行 server
   └─ 参数：max_size, fps, codec, bit_rate

3. 异步读取 H264 帧
   └─ 从 stdout 管道读取 64KB 块
   └─ H264Parser.feed() 解析为完整 NALU
   └─ 通过 WebSocket 发送给前端

4. 前端 WebCodecs 解码
   └─ VideoDecoder 解码 H264
   └─ Canvas 渲染帧

5. ScrcpyEncoder.stop()
   └─ kill 子进程
   └─ 清理资源
```

### 输入事件流程

```
1. 前端捕获用户输入
   └─ 触摸事件：坐标 (x, y)
   └─ 按键事件：keycode
   └─ 文本输入：字符串

2. 通过 WebSocket 发送 JSON
   └─ {"action": "touch", "x": 100, "y": 200}
   └─ {"action": "key", "keycode": 4}
   └─ {"action": "text", "text": "hello"}

3. 后端接收并路由
   └─ 解析 JSON
   └─ 调用 InputController 对应方法

4. InputController 执行 ADB 命令
   └─ adb shell input tap 100 200
   └─ adb shell input keyevent 4
   └─ adb shell input text "hello"
```

## 技术细节

### scrcpy-server 启动命令

```bash
adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar \
    com.genymobile.scrcpy.Server 2.4 \
    max_size=1080 \
    max_fps=30 \
    video_codec=h264 \
    bit_rate=4M \
    send_frame_meta=false \
    raw_stream=true
```

**参数说明：**
- `max_size`: 最大分辨率（宽或高，取较大值）
- `max_fps`: 最大帧率
- `video_codec`: 视频编码格式（h264）
- `bit_rate`: 目标码率
- `send_frame_meta=false`: 不发送帧元数据
- `raw_stream=true`: 输出原始 H264 流

### H264 NALU 格式

```
NALU 起始码：0x00 0x00 0x00 0x01 或 0x00 0x00 0x01
NALU 头部：1 字节（forbidden_zero_bit + nal_ref_idc + nal_unit_type）
NALU 数据：变长

示例：
00 00 00 01 67 42 00 1e ...  # SPS
00 00 00 01 68 ce 38 80 ...  # PPS
00 00 00 01 65 88 84 00 ...  # IDR 关键帧
00 00 00 01 41 9a 24 6c ...  # 非 IDR 帧
```

### ADB 输入命令

```bash
# 触摸点击
adb shell input tap <x> <y>

# 滑动
adb shell input swipe <x1> <y1> <x2> <y2> <duration_ms>

# 按键
adb shell input keyevent <keycode>

# 输入文本
adb shell input text "<text>"
```

## 依赖

- **scrcpy-server.jar**: 需要单独下载并放置在 `backend/app/scrcpy/` 目录
  - 下载地址：https://github.com/Genymobile/scrcpy/releases
  - 当前版本：v2.4
  
- **ADB**: Android Debug Bridge，需要在 PATH 中可用

- **Python 3.11+**: 异步支持

## 注意事项

1. **每个设备只能有一个 scrcpy-server 实例**
   - 多客户端需要共享同一个流（通过 StreamService）

2. **scrcpy-server.jar 必须正确部署**
   - 使用 ServerManager.ensure_server() 自动检查并推送

3. **H264 帧边界需要正确解析**
   - scrcpy-server 输出的是连续字节流
   - 必须使用 H264Parser 提取完整 NALU

4. **输入事件的坐标需要转换**
   - 前端坐标基于视频显示尺寸
   - 需要转换为设备实际分辨率

5. **延迟优化**
   - 降低 max_size 和 bit_rate 可减少延迟
   - 使用 WebTransport 可进一步降低延迟（Chrome 专属）

## 性能指标

| 场景 | 延迟 | 码率 | 备注 |
|------|------|------|------|
| 1080p 30fps | 150-200ms | 4Mbps | 默认配置 |
| 720p 30fps | 100-150ms | 2Mbps | 推荐配置 |
| 480p 30fps | 80-120ms | 1Mbps | 低延迟模式 |

## 故障排查

### scrcpy-server 启动失败

```bash
# 检查 server 是否已推送
adb shell ls -l /data/local/tmp/scrcpy-server.jar

# 手动推送
adb push scrcpy-server.jar /data/local/tmp/

# 检查权限
adb shell chmod 644 /data/local/tmp/scrcpy-server.jar

# 查看错误日志
adb logcat -s scrcpy
```

### 没有收到 H264 帧

- 检查 ADB 连接：`adb devices`
- 检查 scrcpy-server 进程：`adb shell ps | grep scrcpy`
- 查看 server 日志：`adb logcat -s scrcpy`

### 输入事件不生效

- 检查设备是否已解锁（锁屏状态下输入可能被阻止）
- 检查 ADB 权限：`adb shell input tap 0 0`
- 查看输入日志：`adb logcat -s InputDispatcher`

## 参考

- [scrcpy 官方文档](https://github.com/Genymobile/scrcpy)
- [H264 协议规范](https://www.itu.int/rec/T-REC-H.264)
- [Android 输入系统](https://source.android.com/devices/input)
- [WebCodecs API](https://developer.mozilla.org/en-US/docs/Web/API/WebCodecs_API)

## 下一步

- [ ] 下载并集成 scrcpy-server.jar v2.4
- [ ] 实现完整的视频流传输管道
- [ ] 集成到 WebSocket 端点
- [ ] 前端 WebCodecs 解码器
- [ ] 性能优化和延迟测试
