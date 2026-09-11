# 方案 13：scrcpy 二进制控制协议重构

> 状态：**待审批**
> 日期：2026-09-11
> 目标：将输入延迟从 50-200ms 降低到 <5ms，复用 scrcpy 开源协议

---

## 一、问题分析

### 1.1 当前输入延迟根因

当前输入链路（以 `touch` 为例）：

```
浏览器点击 → WebSocket JSON → 后端 _handle_input()
→ asyncio.create_subprocess_exec("adb", "-s", device_id, "shell", "input", "tap", x, y)
→ adb 进程启动 → USB/WiFi → Android input 子系统 → 设备响应
```

**瓶颈**：`asyncio.create_subprocess_exec` 每次创建一个新进程，进程启动开销约 50-200ms。

### 1.2 scrcpy 原生方案

```
浏览器点击 → WebSocket JSON → 后端 ControlSender
→ struct.pack() 序列化为 32 字节二进制
→ control_socket.send() → USB → Android InputManager.injectInputEvent()
```

**优势**：无需创建子进程，直接写入已建立的 TCP socket，延迟 <5ms。

### 1.3 代码走查：已有的控制 socket

**文件**：`backend/app/infrastructure/stream/scrcpy.py` 第 282-295 行

```python
# 8.5 建立第二个连接（控制 socket）
_, control_writer = await asyncio.wait_for(
    asyncio.open_connection("127.0.0.1", self._local_port),
    timeout=3.0,
)
self._control_writer = control_writer
```

**发现**：控制 socket 已建立，但完全没有用于输入发送！只用于清理时关闭。

### 1.4 py-scrcpy-client 的完整实现

**文件**：`/tmp/py-scrcpy-client/scrcpy/control.py`

```python
class ControlSender:
    @inject(const.TYPE_INJECT_TOUCH_EVENT)  # type = 2
    def touch(self, x, y, action, touch_id):
        return struct.pack(">BqiiHHHii",
            action, touch_id, x, y,
            screen_width, screen_height,
            0xFFFF,  # pressure
            1, 1)    # action_button, buttons

    @inject(const.TYPE_INJECT_KEYCODE)  # type = 0
    def keycode(self, keycode, action, repeat):
        return struct.pack(">Biii", action, keycode, repeat, 0)

    @inject(const.TYPE_INJECT_TEXT)  # type = 1
    def text(self, text):
        buffer = text.encode("utf-8")
        return struct.pack(">i", len(buffer)) + buffer
```

**结论**：py-scrcpy-client 的控制协议实现完整且经过验证，可以直接复用其格式。

### 1.5 代码走查验证：协议格式与 scrcpy-server v2.4 完全匹配

通过对比 scrcpy 官方源码和服务端解析代码，验证 py-scrcpy-client 的格式正确性：

#### 触摸事件格式验证

**服务端解析**（`ControlMessageReader.java`）：
```java
private ControlMessage parseInjectTouchEvent() throws IOException {
    int action = dis.readUnsignedByte();           // 1 byte: action
    long pointerId = dis.readLong();               // 8 bytes: pointer_id
    Position position = parsePosition();           // 12 bytes: x(4)+y(4)+width(2)+height(2)
    float pressure = Binary.u16FixedPointToFloat(dis.readShort());  // 2 bytes
    int actionButton = dis.readInt();              // 4 bytes
    int buttons = dis.readInt();                   // 4 bytes
    // ...
}
```

**Position 格式**（12 字节）：
```java
private Position parsePosition() throws IOException {
    int x = dis.readInt();              // 4 bytes: x 坐标
    int y = dis.readInt();              // 4 bytes: y 坐标
    int screenWidth = dis.readUnsignedShort();   // 2 bytes: 屏幕宽度
    int screenHeight = dis.readUnsignedShort();  // 2 bytes: 屏幕高度
    return new Position(x, y, screenWidth, screenHeight);
}
```

**客户端序列化**（`control_msg.c`）：
```c
buf[1] = msg->inject_touch_event.action;           // 1 byte
sc_write64be(&buf[2], msg->inject_touch_event.pointer_id);  // 8 bytes
write_position(&buf[10], &msg->inject_touch_event.position); // 12 bytes
// write_position: x(4) + y(4) + width(2) + height(2)
uint16_t pressure = sc_float_to_u16fp(msg->inject_touch_event.pressure);
sc_write16be(&buf[22], pressure);                  // 2 bytes
sc_write32be(&buf[24], msg->inject_touch_event.action_button); // 4 bytes
sc_write32be(&buf[28], msg->inject_touch_event.buttons);       // 4 bytes
return 32;  // 总计 32 字节
```

**py-scrcpy-client 的 Python 实现**：
```python
package = struct.pack(">B", TYPE_INJECT_TOUCH_EVENT)  # 1 byte: type=2
package += struct.pack(">BqiiHHHii",
    action,        # B: 1 byte - action
    pointer_id,    # q: 8 bytes - pointer_id (signed long long)
    x, y,          # ii: 4+4=8 bytes - 坐标
    screen_width,  # H: 2 bytes - 屏幕宽度
    screen_height, # H: 2 bytes - 屏幕高度
    0xFFFF,        # H: 2 bytes - pressure (max)
    1, 1)          # ii: 4+4=8 bytes - action_button, buttons
# 总计: 1 + (1+8+8+2+2+2+8) = 32 字节 ✓
```

**验证结果**：✅ 格式完全匹配，32 字节，字段顺序和类型一致。

#### 按键事件格式验证

**服务端解析**：
```java
private ControlMessage parseInjectKeycode() throws IOException {
    int action = dis.readUnsignedByte();  // 1 byte
    int keycode = dis.readInt();          // 4 bytes
    int repeat = dis.readInt();           // 4 bytes
    int metaState = dis.readInt();        // 4 bytes
    // 总计: 1 + 4 + 4 + 4 = 13 字节
}
```

**py-scrcpy-client 实现**：
```python
package = struct.pack(">B", TYPE_INJECT_KEYCODE)  # 1 byte: type=0
package += struct.pack(">Biii",
    action,    # B: 1 byte
    keycode,   # i: 4 bytes
    repeat,    # i: 4 bytes
    0)         # i: 4 bytes (metaState)
# 总计: 1 + (1+4+4+4) = 13 字节 ✓
```

**验证结果**：✅ 格式完全匹配。

#### 文本事件格式验证

**服务端解析**：
```java
private ControlMessage parseInjectText() throws IOException {
    String text = parseString();  // 4 bytes length + text
    return ControlMessage.createInjectText(text);
}
```

**py-scrcpy-client 实现**：
```python
package = struct.pack(">B", TYPE_INJECT_TEXT)  # 1 byte: type=1
buffer = text.encode("utf-8")
package += struct.pack(">i", len(buffer)) + buffer  # 4 bytes + text
# 总计: 1 + 4 + len(text) 字节 ✓
```

**验证结果**：✅ 格式完全匹配。

#### 关键设计决策验证

**Q1: pointer_id 使用 -1 是否正确？**
- scrcpy 源码定义：`#define SC_POINTER_ID_MOUSE UINT64_C(-1)`
- py-scrcpy-client 默认使用 `-1` 作为虚拟触摸 ID
- **结论**：✅ 正确，-1 表示鼠标/虚拟触摸

**Q2: 控制 socket 丢弃 reader 是否安全？**
- scrcpy 控制消息是**单向的**（客户端 → 服务端）
- 服务端不会发送任何响应到控制 socket
- **结论**：✅ 安全，只需要 writer

**Q3: pressure 使用 0xFFFF (65535) 是否正确？**
- scrcpy 使用 `u16FixedPoint` 格式，范围 [0, 1]
- 0xFFFF 表示最大压力（1.0）
- py-scrcpy-client 使用 `0xFFFF` 作为固定值
- **结论**：✅ 正确，模拟最大压力触摸

**Q4: action_button 和 buttons 使用 1 是否正确？**
- `action_button`: 触发事件的动作按钮
- `buttons`: 当前按下的按钮状态
- scrcpy 中 `1` 表示 `AMOTION_EVENT_BUTTON_PRIMARY`（主按钮/左键）
- **结论**：✅ 正确，模拟鼠标左键点击

---

## 二、实施方案

### 2.1 改动范围

| 文件 | 动作 | 说明 |
|------|------|------|
| `backend/app/scrcpy/control_sender.py` | **新建** | scrcpy 二进制控制协议 |
| `backend/app/infrastructure/stream/scrcpy.py` | **重构** | 使用 ControlSender + 保存分辨率 |
| `backend/app/interfaces/ws/video.py` | **重构** | 通过 encoder 发送输入 |
| `backend/app/application/stream_service.py` | **修改** | 提供 `get_encoder()` |
| `backend/app/scrcpy/input_controller.py` | **删除** | 被 control_sender.py 替代 |
| `backend/app/scrcpy/__init__.py` | **修改** | 移除旧导出 |

### 2.2 详细步骤

#### 步骤 1：新建 `control_sender.py`

参考 py-scrcpy-client 的 `control.py`，实现异步版本。

```python
# backend/app/scrcpy/control_sender.py
"""
scrcpy 二进制控制协议
=====================
参考 py-scrcpy-client 的 control.py 实现。
通过控制 socket 发送二进制消息到 scrcpy-server。
"""
import struct
import asyncio
from typing import Optional

# 控制消息类型（与 scrcpy 协议一致）
TYPE_INJECT_KEYCODE = 0
TYPE_INJECT_TOUCH_EVENT = 2
TYPE_INJECT_TEXT = 1
TYPE_INJECT_SCROLL_EVENT = 3
TYPE_BACK_OR_SCREEN_ON = 4

# 触摸动作
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2


class ControlSender:
    """scrcpy 控制消息发送器。"""

    def __init__(self, writer: asyncio.StreamWriter,
                 resolution: tuple[int, int]):
        self._writer = writer
        self._resolution = resolution  # (width, height)
        self._lock = asyncio.Lock()

    async def touch(self, x: int, y: int, action: int,
                    pointer_id: int = -1):
        """发送触摸事件（32 字节）。"""
        package = struct.pack(">B", TYPE_INJECT_TOUCH_EVENT)
        package += struct.pack(">BqiiHHHii",
            action, pointer_id,
            int(x), int(y),
            self._resolution[0], self._resolution[1],
            0xFFFF, 1, 1)
        await self._send(package)

    async def keycode(self, keycode: int, action: int = ACTION_DOWN,
                      repeat: int = 0):
        """发送按键事件。"""
        package = struct.pack(">B", TYPE_INJECT_KEYCODE)
        package += struct.pack(">Biii", action, keycode, repeat, 0)
        await self._send(package)

    async def text(self, text: str):
        """发送文本输入。"""
        package = struct.pack(">B", TYPE_INJECT_TEXT)
        buffer = text.encode("utf-8")
        package += struct.pack(">i", len(buffer)) + buffer
        await self._send(package)

    async def _send(self, data: bytes):
        """线程安全发送。"""
        async with self._lock:
            self._writer.write(data)
            await self._writer.drain()
```

**关键设计决策**：
- `pointer_id` 默认 `-1`（虚拟触摸 ID），与 py-scrcpy-client 一致
- `asyncio.Lock` 保护并发写入（多个 WebSocket 客户端共用同一 socket）
- `resolution` 在构造时传入，避免每次调用都查询

#### 步骤 2：重构 `scrcpy.py`

**改动 1**：保存分辨率和 ControlSender 实例

```python
# 在握手读取后保存分辨率
self._resolution = (width, height)
self._control_sender: ControlSender | None = None

# 在控制 socket 建立后创建 ControlSender
if self._control_writer:
    self._control_sender = ControlSender(
        self._control_writer, self._resolution)
```

**改动 2**：提供 `send_input()` 方法

```python
async def send_input(self, data: dict):
    """通过控制 socket 发送输入事件。"""
    if not self._control_sender:
        # 回退到 adb shell input（控制 socket 不可用时）
        await self._fallback_adb_input(data)
        return

    action = data.get("action")
    if action == "touch":
        await self._control_sender.touch(
            data["x"], data["y"], ACTION_DOWN)
        await self._control_sender.touch(
            data["x"], data["y"], ACTION_UP)
    elif action == "swipe":
        # 参考 py-scrcpy-client 的 swipe() 实现：
        # DOWN → N 个 MOVE → UP
        await self._send_swipe(data)
    elif action == "key":
        await self._control_sender.keycode(data["keycode"])
    elif action == "text":
        await self._control_sender.text(data["text"])
```

**改动 3**：滑动实现（参考 py-scrcpy-client）

```python
async def _send_swipe(self, data: dict):
    """连续 MOVE 事件滑动（参考 py-scrcpy-client swipe()）。"""
    x1, y1 = data["x1"], data["y1"]
    x2, y2 = data["x2"], data["y2"]
    duration = data.get("duration", 300)

    await self._control_sender.touch(x1, y1, ACTION_DOWN)

    # 计算步长和步数
    step_length = 5  # 像素/步
    steps_delay = duration / 1000.0 / max(
        abs(x2-x1), abs(y2-y1), 1) * step_length

    # 线性插值 MOVE 事件
    dx = x2 - x1
    dy = y2 - y1
    total_steps = max(abs(dx), abs(dy)) // step_length or 1

    for i in range(1, total_steps + 1):
        ratio = i / total_steps
        nx = x1 + int(dx * ratio)
        ny = y1 + int(dy * ratio)
        await self._control_sender.touch(nx, ny, ACTION_MOVE)
        await asyncio.sleep(min(steps_delay, 0.01))

    await self._control_sender.touch(x2, y2, ACTION_UP)
```

**改动 4**：清理调试日志

移除第 357-418 行过度详细的 `_read_count`、`_read_bytes`、`_yield_count` 等调试变量和日志。保留关键的 info/error 日志。

#### 步骤 3：重构 `video.py`

```python
async def _handle_input(device_id: str, data: dict,
                        stream_service: StreamService):
    """通过 ScrcpyEncoder 的控制 socket 发送输入。"""
    encoder = stream_service.get_encoder(device_id)
    if encoder:
        await encoder.send_input(data)
    else:
        logger.warning("no_encoder_for_input", device=device_id)
```

#### 步骤 4：修改 `stream_service.py`

```python
def get_encoder(self, device_id: str) -> ScrcpyEncoder | None:
    """获取指定设备的编码器实例。"""
    return self.encoders.get(device_id)
```

#### 步骤 5：删除 `input_controller.py`

不再需要 adb shell input 的封装。

---

## 三、风险分析与应对

### 风险 1：控制 socket 建立失败

**现象**：scrcpy-server 启动后，控制 socket 连接超时或被拒绝。
**影响**：输入功能不可用。
**应对**：
- 保留 `adb shell input` 作为回退方案
- `send_input()` 检查 `self._control_sender` 是否为 None
- 如果为 None，自动降级到 adb 命令
- 日志记录降级事件，便于诊断

```python
async def send_input(self, data: dict):
    if not self._control_sender:
        logger.warning("control_socket_unavailable_fallback_to_adb",
                       device=self._device_id)
        await self._fallback_adb_input(data)
        return
    # ... 正常二进制协议发送
```

### 风险 2：控制 socket 写入阻塞

**现象**：`writer.drain()` 长时间阻塞，导致输入延迟反而增大。
**影响**：输入事件积压。
**应对**：
- 使用 `asyncio.wait_for(writer.drain(), timeout=1.0)` 设置超时
- 超时后记录警告并丢弃事件（不阻塞视频流）
- 考虑增加写入队列，超过阈值时丢弃旧事件

### 风险 3：分辨率变化（屏幕旋转）

**现象**：设备旋转屏幕后，控制消息中的 `screen_width/screen_height` 与实际不匹配。
**影响**：触摸坐标映射错误。
**应对**：
- 当前阶段：锁定屏幕方向（scrcpy-server 参数 `lock_screen_orientation`）
- 后续优化：监听视频帧尺寸变化，动态更新 resolution
- 参考 scrcpy 源码 `CaptureControl.RESET_REASON_DISPLAY_PROPERTIES_CHANGED`

### 风险 4：并发写入冲突

**现象**：多个 WebSocket 客户端同时发送输入，导致二进制消息交错。
**影响**：控制消息格式损坏，设备收到无效数据。
**应对**：
- `ControlSender` 使用 `asyncio.Lock` 保护每次写入
- 当前架构下每个设备只有一个 ScrcpyEncoder，不存在多客户端问题
- 但防御性编程，仍保留锁

### 风险 5：`struct.pack` 格式不匹配

**现象**：scrcpy-server 版本不同导致协议格式差异。
**影响**：服务端拒绝或错误解析消息。
**应对**：
- 使用 `scrcpy-server v2.4`，与 py-scrcpy-client 一致
- 触摸事件格式（32 字节）已在 scrcpy 多个版本中保持稳定
- 参考 scrcpy 源码 `app/src/control_msg.c` 中的序列化格式
- 如果未来升级 server 版本，需要同步更新 `struct.pack` 格式

### 风险 6：视频流中断但控制 socket 仍在

**现象**：视频 socket 断开，但控制 socket 未检测到。
**影响**：用户看到黑屏但输入仍在发送。
**应对**：
- `stop()` 方法同时关闭两个 socket
- 视频流中断时触发 `stop()`，自动清理控制 socket

---

## 四、验证计划

### 4.1 单元测试

```python
# tests/unit/test_control_sender.py
class TestControlSender:
    def test_touch_event_format():
        """验证触摸事件序列化为 32 字节。"""
        writer = MockStreamWriter()
        sender = ControlSender(writer, (1080, 1920))
        await sender.touch(100, 200, ACTION_DOWN)
        assert len(writer.data) == 32
        assert writer.data[0] == TYPE_INJECT_TOUCH_EVENT  # type

    def test_keycode_event_format():
        """验证按键事件序列化。"""
        ...
```

### 4.2 集成测试

1. 启动后端 → 连接设备 → 打开视频流
2. 通过 WebSocket 发送 touch 事件
3. 验证设备收到触摸（屏幕上有响应）
4. 发送 swipe 事件 → 验证连续 MOVE 事件
5. 发送 key 事件 → 验证 HOME/BACK 响应

### 4.3 端到端测试（浏览器）

1. 打开视频播放器页面
2. 点击设备屏幕上的按钮 → 验证响应延迟 < 100ms
3. 滑动列表 → 验证平滑滚动
4. 使用导航键（HOME/BACK）→ 验证功能正常

---

## 五、不在本次范围内

1. **前端 MOVE 事件连续发送** — 当前后端生成 MOVE 事件即可，前端优化可后续进行
2. **视频流协议变更** — `send_frame_meta=false` + raw H264 已经是正确做法，无需改动
3. **多指针触摸** — 当前仅支持单点触摸，多点触控可后续扩展
4. **滚动事件** — py-scrcpy-client 支持，但前端暂未实现，可后续添加
5. **剪贴板同步** — scrcpy 支持但复杂度高，优先级低

---

## 六、实施注意事项

### 6.1 控制 socket 建立时序

当前代码中控制 socket 建立在视频 socket 之后（第 282-295 行）。需要确保：

1. **分辨率必须在控制 socket 建立前读取**：分辨率从视频 socket 读取（第 332-349 行），必须在创建 ControlSender 之前完成
2. **控制 socket 建立失败不是致命的**：当前代码已经处理（第 293-295 行），但需要确保后续代码检查 `self._control_writer` 是否为 None

**实施顺序**：
```
1. 读取 dummy byte
2. 读取设备名
3. 读取分辨率（width, height）
4. 建立控制 socket
5. 创建 ControlSender（需要 writer + resolution）
```

### 6.2 分辨率读取失败的处理

如果分辨率读取超时或失败，`self._resolution` 可能为 None 或默认值。需要：

1. 在握手阶段设置合理的超时（当前 2-3 秒）
2. 如果分辨率读取失败，仍然创建 ControlSender，但使用默认值 `(0, 0)`
3. 在 `send_input()` 中检查分辨率是否有效，无效时回退到 adb shell input

```python
if self._resolution == (0, 0):
    logger.warning("resolution_unknown_fallback_to_adb")
    await self._fallback_adb_input(data)
    return
```

### 6.3 滑动实现的性能优化

当前滑动实现在每个 MOVE 事件后 `await asyncio.sleep()`，可能导致：

1. **滑动延迟**：如果 `steps_delay` 较大，总滑动时间会超过用户指定的 `duration`
2. **事件积压**：快速滑动时，MOVE 事件可能在队列中积压

**优化方案**：
1. 限制最大步数（如 100 步），避免过多 MOVE 事件
2. 使用 `asyncio.gather` 批量发送 MOVE 事件（但需要保持顺序）
3. 如果 `duration` 很短（< 100ms），直接发送 DOWN + UP，跳过 MOVE

```python
async def _send_swipe(self, data: dict):
    x1, y1 = data["x1"], data["y1"]
    x2, y2 = data["x2"], data["y2"]
    duration = data.get("duration", 300)

    await self._control_sender.touch(x1, y1, ACTION_DOWN)

    # 短距离或短时间：直接 UP
    distance = ((x2-x1)**2 + (y2-y1)**2) ** 0.5
    if distance < 10 or duration < 100:
        await self._control_sender.touch(x2, y2, ACTION_UP)
        return

    # 限制最大步数
    step_length = 5
    total_steps = min(int(distance / step_length), 100)
    step_delay = duration / 1000.0 / total_steps

    dx = x2 - x1
    dy = y2 - y1
    for i in range(1, total_steps + 1):
        ratio = i / total_steps
        nx = x1 + int(dx * ratio)
        ny = y1 + int(dy * ratio)
        await self._control_sender.touch(nx, ny, ACTION_MOVE)
        await asyncio.sleep(step_delay)

    await self._control_sender.touch(x2, y2, ACTION_UP)
```

### 6.4 回退方案的实现

需要保留 `adb shell input` 作为回退方案，实现为独立方法：

```python
async def _fallback_adb_input(self, data: dict):
    """回退到 adb shell input（控制 socket 不可用时）。"""
    action = data.get("action")
    if action == "touch":
        x, y = data["x"], data["y"]
        await asyncio.create_subprocess_exec(
            "adb", "-s", self._device_id, "shell", "input", "tap", str(x), str(y))
    elif action == "swipe":
        x1, y1 = data["x1"], data["y1"]
        x2, y2 = data["x2"], data["y2"]
        duration = data.get("duration", 300)
        await asyncio.create_subprocess_exec(
            "adb", "-s", self._device_id, "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(duration))
    elif action == "key":
        keycode = data["keycode"]
        await asyncio.create_subprocess_exec(
            "adb", "-s", self._device_id, "shell", "input", "keyevent", str(keycode))
    elif action == "text":
        text = data["text"]
        await asyncio.create_subprocess_exec(
            "adb", "-s", self._device_id, "shell", "input", "text", text)
```

### 6.5 日志清理策略

当前 `scrcpy.py` 中有大量调试日志（第 357-418 行），需要清理：

**保留的日志**：
- `starting_scrcpy_server_encoder` — 启动信息
- `connected_to_scrcpy_server` — 连接成功
- `control_connection_established` — 控制连接建立
- `scrcpy_resolution` — 分辨率信息
- `socket_read_error` — 读取错误
- `stopping_scrcpy_encoder` — 停止信息

**删除的日志**：
- `socket_data_received`（每 100 次打印一次）— 过于详细
- `yielding_data`（每 100 次打印一次）— 过于详细
- `socket_queue_full_dropping_chunk` — 队列满的情况（保留为 warning）

**变量清理**：
- 删除 `_read_count`、`_read_bytes`、`_yield_count`、`_yield_bytes`
- 删除 `_read_start` 时间戳

---

## 七、实施步骤清单

- [ ] 1. 新建 `backend/app/scrcpy/control_sender.py`
- [ ] 2. 重构 `backend/app/infrastructure/stream/scrcpy.py`
  - [ ] 2.1 保存分辨率到 `self._resolution`
  - [ ] 2.2 创建 `ControlSender` 实例
  - [ ] 2.3 实现 `send_input()` 方法
  - [ ] 2.4 实现 `_send_swipe()` 方法
  - [ ] 2.5 实现 `_fallback_adb_input()` 方法
  - [ ] 2.6 清理调试日志
- [ ] 3. 重构 `backend/app/interfaces/ws/video.py`
  - [ ] 3.1 修改 `_handle_input` 调用 `encoder.send_input()`
- [ ] 4. 修改 `backend/app/application/stream_service.py`
  - [ ] 4.1 添加 `get_encoder()` 方法
- [ ] 5. 删除 `backend/app/scrcpy/input_controller.py`
- [ ] 6. 更新 `backend/app/scrcpy/__init__.py`
- [ ] 7. 更新进度追踪文档
- [ ] 8. 验证 TypeScript 编译
- [ ] 9. 验证 Python 启动无报错
- [ ] 10. 端到端测试（浏览器点击、滑动、按键）
