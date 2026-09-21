# 视频流服务方案 A 实施记录

## 实施时间
2026-09-08 16:40

## 方案概述
使用官方 scrcpy.exe 工具进行视频录制和流式传输。

## 实现原理
1. 调用 scrcpy.exe 的 `--record` 选项将视频录制到临时 MP4 文件
2. 使用后台任务持续监控文件大小变化
3. 实时读取新增数据并通过 asyncio.Queue 传递给主循环
4. 主循环通过 async generator yield 数据给 WebSocket

## 核心代码

### ScrcpyEncoder 类
位置：`backend/app/infrastructure/stream/scrcpy.py`

主要方法：
- `start()`: 启动 scrcpy.exe 进程并初始化文件监控
- `_read_video_file_continuously()`: 后台任务，持续读取视频文件
- `stop()`: 清理资源（终止进程、删除临时文件）

### 配置更新
位置：`config/settings.py`

新增配置项：
```python
class StreamConfig(BaseSettings):
    scrcpy_path: str = Field(default="D:/scrcpy-win64-v4.1/scrcpy.exe", alias="SCRCPY_PATH")
```

## scrcpy.exe 命令参数
```bash
D:\scrcpy-win64-v4.1\scrcpy.exe \
  --serial 192.168.8.22:5555 \
  --max-size 1080 \
  --video-bit-rate 4000000 \
  --max-fps 30 \
  --record <temp_file>.mp4 \
  --no-playback \
  --no-control
```

## 测试结果
```
帧 1: 48 字节 (MP4 文件头)
帧 2: 262144 字节 (256KB 视频数据)
帧 3: 19348 字节 (剩余数据)
总帧数: 3
总字节: 281540
结果: SUCCESS
```

## 已知问题
1. scrcpy.exe 进程在录制约 33 秒后自动退出（returncode=0）
   - 可能原因：`--no-control` 和 `--no-playback` 组合导致
   - 解决方案：需要调整参数或添加保活机制

2. 端口冲突警告
   ```
   ERROR: bind: [10013] An attempt was made to access a socket in a way forbidden by its access permissions.
   WARN: Could not listen on port 27183, retrying on 27184
   ```
   - 原因：端口 27183 被其他进程占用
   - 影响：scrcpy 自动切换到 27184 端口，功能正常

3. 音频不支持
   ```
   [server] WARN: Audio disabled: it is not supported before Android 11
   ```
   - 原因：设备运行 Android 9，不支持音频录制
   - 影响：仅视频流，无音频

## 下一步计划
1. 解决 scrcpy.exe 进程自动退出问题
   - 测试不同的参数组合
   - 考虑添加 `--stay-awake` 或 `--turn-screen-off` 选项
   - 实现进程重启机制

2. 集成到 StreamService
   - 确保多设备支持正常工作
   - 测试并发录制多个设备

3. WebSocket 集成
   - 测试通过 WebSocket 发送视频数据
   - 前端解码和显示

4. 性能优化
   - 减少文件读取延迟
   - 优化数据块大小
   - 考虑使用内存映射文件

## 方案对比

### 方案 A（当前实现）：使用官方 scrcpy.exe
**优点：**
- 使用官方工具，稳定性有保障
- 自动处理所有复杂的连接和协议问题
- 代码简洁，易于维护

**缺点：**
- 依赖外部工具（scrcpy.exe）
- 视频格式为 MP4（需要解码）
- 进程管理相对复杂
- 延迟较高（文件 I/O）

### 方案 C（未来升级）：使用 py-scrcpy-client
**优点：**
- 纯 Python 实现，更容易集成
- 直接获取 H.264 原始流
- 延迟更低
- 更细粒度的控制

**缺点：**
- 依赖 av 库（编译问题）
- 需要维护更多代码

## 结论
方案 A 成功实现了基本的视频流功能，可以作为过渡方案使用。
如果未来需要更好的性能和更低的延迟，可以考虑升级到方案 C。

## 参考资源
- [scrcpy 官方文档](https://github.com/Genymobile/scrcpy)
- [ws-scrcpy 项目](https://github.com/NetrisTV/ws-scrcpy)
- [py-scrcpy-client](https://github.com/leng-yue/py-scrcpy-client)
