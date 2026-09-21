"""
测试调试 WebSocket 端点
========================

测试 /ws/debug/{session_id} 端点的实时日志推送功能。
"""
import asyncio
import sys
import json
from pathlib import Path

# 添加 backend 到路径
sys.path.insert(0, str(Path(__file__).parent / "backend"))

try:
    import websockets
    import httpx
except ImportError:
    print("需要安装依赖: pip install websockets httpx")
    sys.exit(1)


async def test_debug_websocket():
    """测试调试 WebSocket 连接和日志订阅"""
    print("=== 调试 WebSocket 测试 ===\n")

    device_id = "192.168.8.22:5555"

    # 1. 创建调试会话
    print("1. 创建调试会话...")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:8000/api/debug/sessions",
            params={"device_id": device_id, "user_id": "test-user"}
        )
        if resp.status_code != 200:
            print(f"[FAIL] 创建会话失败: {resp.status_code}")
            print(resp.text)
            return False

        session_data = resp.json()
        session_id = session_data["session_id"]
        print(f"[OK] 会话 ID: {session_id}\n")

    # 2. 连接 WebSocket
    ws_url = f"ws://localhost:8000/ws/debug/{session_id}"
    print(f"2. 连接 WebSocket: {ws_url}")

    try:
        async with websockets.connect(ws_url) as ws:
            print("[OK] WebSocket 连接成功\n")

            # 3. 发送订阅请求
            print("3. 发送订阅请求...")
            await ws.send(json.dumps({"op": "subscribe"}))

            # 等待订阅确认
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(response)
                print(f"[OK] 收到响应: {msg}")
            except asyncio.TimeoutError:
                print("[FAIL] 订阅超时")
                return False

            print()

            # 4. 等待日志推送（10 秒）
            print("4. 等待日志推送（10 秒）...")
            log_count = 0

            try:
                while True:
                    response = await asyncio.wait_for(ws.recv(), timeout=10)
                    msg = json.loads(response)

                    if msg.get("type") == "log":
                        log_count += 1
                        entry = msg["entry"]
                        if log_count <= 5:
                            print(f"  日志 #{log_count}: [{entry['level']}] {entry['tag']}: {entry['message'][:50]}...")

            except asyncio.TimeoutError:
                pass

            print(f"\n[OK] 收到 {log_count} 条日志")

            # 5. 测试 shell 命令
            # 注意：由于 WS 已订阅日志，shell_output 会混在持续的 log 消息中。
            # 需要循环读取消息，直到找到 shell_output 类型。
            print("\n5. 测试 shell 命令...")
            await ws.send(json.dumps({"op": "exec", "command": "echo 'Hello from test'"}))

            shell_received = False
            log_during_shell = 0
            try:
                while True:
                    response = await asyncio.wait_for(ws.recv(), timeout=5)
                    msg = json.loads(response)
                    if msg.get("type") == "shell_output":
                        print(f"[OK] Shell 输出: {msg['output']}")
                        shell_received = True
                        break
                    elif msg.get("type") == "log":
                        log_during_shell += 1
            except asyncio.TimeoutError:
                print(f"[FAIL] Shell 命令超时（期间收到 {log_during_shell} 条日志）")

            if not shell_received:
                print("[FAIL] 未收到 shell_output")

            # 6. 取消订阅
            # 同样需要循环读取，因为 log 消息可能在 unsubscribed 之前到达
            print("\n6. 取消订阅...")
            await ws.send(json.dumps({"op": "unsubscribe"}))

            unsub_received = False
            try:
                while True:
                    response = await asyncio.wait_for(ws.recv(), timeout=5)
                    msg = json.loads(response)
                    if msg.get("type") == "unsubscribed":
                        print(f"[OK] 取消订阅成功")
                        unsub_received = True
                        break
                    # 忽略仍在传输中的 log 消息
            except asyncio.TimeoutError:
                print("[FAIL] 取消订阅超时")

            if not unsub_received:
                print("[FAIL] 未收到 unsubscribed 响应")

    except ConnectionRefusedError:
        print("[FAIL] 无法连接到服务器")
        return False
    except Exception as e:
        print(f"[FAIL] 错误: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 7. 关闭会话
    print("\n7. 关闭会话...")
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"http://localhost:8000/api/debug/sessions/{session_id}")
        if resp.status_code == 200:
            print("[OK] 会话已关闭")
        else:
            print(f"[FAIL] 关闭会话失败: {resp.status_code}")

    print(f"\n{'='*60}")
    print("测试完成")
    print(f"{'='*60}")

    if log_count > 0:
        print("\n[SUCCESS] 调试 WebSocket 测试通过！")
        return True
    else:
        print("\n[FAIL] 未收到日志")
        return False


async def main():
    result = await test_debug_websocket()
    return 0 if result else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
