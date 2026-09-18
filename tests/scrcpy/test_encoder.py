"""
视频编码器测试（针对现架构：TCP socket + 队列 + 控制发送器）
================================================================

只测可稳定触达的缝，不启动真实 adb 子进程/socket：
    - push_server 失败 → start 抛 RuntimeError
    - 无 control_sender 时 send_input 回退 adb shell input
    - 短距离 swipe 仅发 down+up
    - stop 释放 process/socket
帧读取/NALU 解析属 WS 层 H264Parser 职责，另有覆盖，此处不再测。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.ports import EncoderOpts
from app.infrastructure.stream.scrcpy import ScrcpyEncoder


@pytest.fixture
def encoder():
    return ScrcpyEncoder()


@pytest.mark.asyncio
async def test_start_raises_when_push_server_fails(encoder, mock_device_id):
    """server 推送失败时 start 立即抛 RuntimeError。"""
    encoder._server_manager.push_server = AsyncMock(return_value=False)
    opts = EncoderOpts(max_size=720, bit_rate="2M", codec="h264", fps=30)

    agen = encoder.start(mock_device_id, opts)
    with pytest.raises(RuntimeError, match="Failed to push scrcpy-server.jar"):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_send_input_falls_back_to_adb_without_control_sender(encoder):
    """无 control_sender/分辨率时 touch 回退到 adb shell input tap。"""
    encoder._device_id = "emulator-5554"
    encoder._control_sender = None
    encoder._resolution = (0, 0)

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as exec_mock:
        await encoder.send_input({"action": "touch", "x": 100, "y": 200})

    args = [str(a) for a in exec_mock.call_args[0]]
    assert "input" in args and "tap" in args and "100" in args and "200" in args


@pytest.mark.asyncio
async def test_send_input_falls_back_on_unknown_action(encoder):
    """未知 action 也走 adb 回退分支（内部仅记 warning，不抛错）。"""
    encoder._device_id = "emulator-5554"
    encoder._control_sender = None
    encoder._resolution = (0, 0)

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        await encoder.send_input({"action": "bogus"})  # 不抛异常


@pytest.mark.asyncio
async def test_send_swipe_short_distance_only_down_and_up(encoder):
    """距离<10 的 swipe 只发 ACTION_DOWN + ACTION_UP，无中间 MOVE。"""
    from app.scrcpy.control_sender import ACTION_DOWN, ACTION_UP

    sender = MagicMock()
    sender.touch = AsyncMock()
    encoder._control_sender = sender
    encoder._resolution = (1080, 1920)

    await encoder._send_swipe({"x1": 10, "y1": 10, "x2": 12, "y2": 10, "duration": 50})

    assert sender.touch.await_count == 2
    acts = [c.args[2] for c in sender.touch.await_args_list]
    assert acts == [ACTION_DOWN, ACTION_UP]


@pytest.mark.asyncio
async def test_stop_terminates_process_and_closes_sockets(encoder):
    """stop 终止进程、关闭视频/控制 socket，并复位状态。"""
    proc = MagicMock()
    proc.returncode = None
    proc.terminate = MagicMock()
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    encoder.process = proc

    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    encoder._writer = writer

    ctrl = MagicMock()
    ctrl.close = MagicMock()
    ctrl.wait_closed = AsyncMock()
    encoder._control_writer = ctrl
    encoder._device_id = "emulator-5554"

    # 端口清理的 adb 子进程 mock 掉
    cleanup = MagicMock()
    cleanup.communicate = AsyncMock(return_value=(b"", b""))
    cleanup.returncode = 0
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=cleanup)):
        await encoder.stop()

    proc.terminate.assert_called()
    writer.close.assert_called()
    ctrl.close.assert_called()
    assert encoder.process is None
    assert encoder._device_id == ""
