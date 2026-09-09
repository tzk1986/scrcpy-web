#!/bin/bash

DEVICE="192.168.8.22:5555"
ADB="D:/tangzk/py/scrcpy-web/tools/adb.exe"

echo "=== 手动测试 scrcpy-server ==="

# 清理之前的进程
echo "1. 清理旧进程..."
$ADB -s $DEVICE shell "pkill -f scrcpy" 2>/dev/null || true

# 清理 logcat
echo "2. 清理 logcat..."
$ADB -s $DEVICE shell "logcat -c"

# 启动 scrcpy-server（后台）
echo "3. 启动 scrcpy-server..."
$ADB -s $DEVICE shell 'CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.genymobile.scrcpy.Server 2.4 max_size=1080 max_fps=30 video_codec=h264 video_bit_rate=4000000 send_frame_meta=false raw_stream=true tunnel_forward=true' &
SERVER_PID=$!

# 等待几秒
echo "4. 等待服务器启动..."
sleep 3

# 检查进程
echo "5. 检查进程..."
$ADB -s $DEVICE shell "ps -A | grep scrcpy" || echo "进程未运行"

# 查看日志
echo "6. 查看日志..."
$ADB -s $DEVICE shell "logcat -d -t 100 | grep -E 'scrcpy|Server|app_process|CLASSPATH'" | head -30

# 清理
echo "7. 清理..."
kill $SERVER_PID 2>/dev/null || true

echo "=== 测试完成 ==="
