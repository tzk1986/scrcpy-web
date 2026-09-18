"""
视频流 WebSocket 端点
========================

层：接口 → WebSocket。

处理 /ws/video/{device_id} 连接，负责：
    1. 接受 WebSocket 连接
    2. 从 StreamService 获取视频流
    3. 解析 H.264 NAL 单元并发送给客户端
    4. 接收客户端的输入事件并转发给设备

协议格式：
    服务端 → 客户端：
        - 初始配置：JSON {"type": "config", "width": 1080, "height": 1920, "codec": "avc1.42E01E"}
          （自适应码率重启后会重新下发一次 config，客户端应重建解码器）
        - 视频帧：二进制消息（H.264 NAL 单元，带起始码）
        - 错误：JSON {"type": "error", "message": "..."}
        - 码率切换预告：JSON {"type": "restarting", "bit_rate": 2000000}
          （编码器即将重启，客户端应暂停回退 watchdog 宽限若干秒）
    客户端 → 服务端：JSON 消息
        { "action": "touch", "x": 100, "y": 200 }
        { "action": "swipe", "x1": 100, "y1": 200, "x2": 300, "y2": 400, "duration": 300 }
        { "action": "key", "keycode": 4 }
        { "action": "text", "text": "hello" }

H.264 流处理：
    - 使用 H264Parser 解析 MP4 容器中的 H.264 NAL 单元
    - 发送 SPS/PPS 作为配置信息
    - 发送 IDR 帧和 P/B 帧作为二进制数据
    - 客户端使用 WebCodecs VideoDecoder 解码

连接生命周期：
    - 客户端连接时，启动视频流
    - 客户端断开时，停止视频流并释放编码器资源
"""

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends

from app.application.stream_service import StreamService
from app.deps import get_stream_service
from app.scrcpy.h264_parser import H264Parser
from app.scrcpy.constants import NALU_TYPE_SPS, NALU_TYPE_PPS
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["video"])


@router.websocket("/ws/video/{device_id}")
async def video_stream(
    websocket: WebSocket,
    device_id: str,
    stream_service: StreamService = Depends(get_stream_service),
) -> None:
    """
    视频流 WebSocket 处理函数。

    流程：
        1. 接受 WebSocket 连接
        2. 启动并发任务：视频流 + 输入处理
        3. 解析 H.264 NAL 单元并发送（SPS/PPS 先发配置）
        4. 同时处理客户端输入事件
        5. 断开时停止视频流
    """
    await websocket.accept()

    parser = H264Parser()
    sps_data = None
    pps_data = None
    config_sent = False
    seen_epoch = stream_service.get_stream_epoch(device_id)

    async def handle_input() -> None:
        """并发处理客户端输入事件；stats 上报喂自适应决策器，
        检测到待生效码率切换时通知客户端进入重启宽限期。"""
        try:
            last_notified = None
            while True:
                data = await websocket.receive_json()
                await _handle_input(device_id, data, stream_service)
                pend = stream_service.peek_pending_bitrate(device_id)
                if pend is not None and pend != last_notified:
                    last_notified = pend
                    try:
                        await websocket.send_json({"type": "restarting", "bit_rate": pend})
                        logger.info("notified_client_restarting",
                                    device=device_id, bit_rate=pend)
                    except Exception:
                        pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("input_handler_stopped", device=device_id, error=str(e))

    # 启动输入处理任务
    input_task = asyncio.create_task(handle_input())

    try:
        # 发送视频流
        frame_count = 0
        chunk_count = 0
        async for chunk in stream_service.start_stream(device_id):
            chunk_count += 1

            # 自适应码率重启：轮次变化时重置解析器，丢弃跨重启的半截 NALU，
            # 并强制重新下发 config（客户端据此重建解码器）
            epoch = stream_service.get_stream_epoch(device_id)
            if epoch != seen_epoch:
                seen_epoch = epoch
                parser = H264Parser()
                sps_data = None
                pps_data = None
                config_sent = False
                logger.info("video_stream_epoch_reset", device=device_id, epoch=epoch)
            if chunk_count <= 3:
                logger.info("video_chunk_received", device=device_id,
                            chunk_size=len(chunk), chunk_count=chunk_count)
            elif chunk_count % 100 == 0:
                # 每 100 个 chunk 记录一次（避免日志过多）
                logger.info("video_chunk_progress", device=device_id,
                           chunk_count=chunk_count, frame_count=frame_count)

            nalus = parser.feed(chunk)

            for nalu in nalus:
                nalu_type = parser.get_nalu_type(nalu)

                if nalu_type == NALU_TYPE_SPS:
                    sps_data = nalu
                    logger.info("sps_received", device=device_id, nalu_size=len(nalu))
                    if sps_data and pps_data and not config_sent:
                        await _send_config(websocket, sps_data, pps_data, stream_service, device_id)
                        config_sent = True
                        logger.info("config_sent", device=device_id)

                elif nalu_type == NALU_TYPE_PPS:
                    pps_data = nalu
                    logger.info("pps_received", device=device_id, nalu_size=len(nalu))
                    if sps_data and pps_data and not config_sent:
                        await _send_config(websocket, sps_data, pps_data, stream_service, device_id)
                        config_sent = True
                        logger.info("config_sent", device=device_id)

                else:
                    if config_sent:
                        await websocket.send_bytes(nalu)
                        frame_count += 1
                        if frame_count <= 5:
                            logger.info("video_frame_sent", device=device_id,
                                       frame_count=frame_count,
                                       nalu_type=nalu_type,
                                       nalu_size=len(nalu))
                    else:
                        if frame_count == 0:
                            logger.info("frame_before_config", device=device_id,
                                        nalu_type=nalu_type, nalu_size=len(nalu))

        logger.info("video_stream_ended", device=device_id,
                     chunks=chunk_count, frames=frame_count,
                     config_sent=config_sent)

    except WebSocketDisconnect:
        logger.info("video_websocket_disconnected", device=device_id)
    except Exception as e:
        logger.error("video_stream_error", device=device_id, error=str(e))
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        # 停止输入处理任务
        input_task.cancel()
        try:
            await input_task
        except asyncio.CancelledError:
            pass
        await stream_service.stop_stream(device_id)


async def _send_config(websocket: WebSocket, sps: bytes, pps: bytes, stream_service: StreamService, device_id: str) -> None:
    """
    发送编解码器配置（SPS/PPS）。

    WebCodecs 需要 SPS/PPS 来初始化解码器。
    将它们以 hex 编码发送给客户端，客户端转为 AVCC 格式后创建 VideoDecoder。
    """
    # 从 SPS 动态提取 codec 信息
    # SPS 格式（包含起始码）：
    # - 起始码（3或4字节）：00 00 01 或 00 00 00 01
    # - NAL 头（1字节）：0x67 表示 SPS
    # - profile_idc（1字节）
    # - constraint_set flags（1字节）
    # - level_idc（1字节）
    #
    # 需要跳过起始码和 NAL 头，提取 profile/constraint/level
    codec = "avc1.42E01E"  # 默认值

    # 查找起始码后的位置
    sps_data_start = 0
    if sps[:4] == b'\x00\x00\x00\x01':
        sps_data_start = 4
    elif sps[:3] == b'\x00\x00\x01':
        sps_data_start = 3

    # sps[sps_data_start] = NAL 头（0x67）
    # sps[sps_data_start + 1] = profile_idc
    # sps[sps_data_start + 2] = constraint_set flags
    # sps[sps_data_start + 3] = level_idc
    if len(sps) >= sps_data_start + 4:
        nal_header = sps[sps_data_start]
        profile_idc = sps[sps_data_start + 1]
        constraint_flags = sps[sps_data_start + 2]
        level_idc = sps[sps_data_start + 3]

        # 构建 codec 字符串：avc1.XXYYZZ
        # XX = profile_idc, YY = constraint_flags, ZZ = level_idc
        codec = f"avc1.{profile_idc:02X}{constraint_flags:02X}{level_idc:02X}"
        logger.info("codec_extracted_from_sps", device=device_id, codec=codec,
                    nal_header=f"0x{nal_header:02X}",
                    profile_idc=f"0x{profile_idc:02X}",
                    constraint_flags=f"0x{constraint_flags:02X}",
                    level_idc=f"0x{level_idc:02X}")
    else:
        logger.warning("sps_too_short_for_codec_extraction", device=device_id, sps_length=len(sps))

    # 获取设备分辨率
    encoder = stream_service.get_encoder(device_id)
    width = encoder.resolution[0] if encoder else 0
    height = encoder.resolution[1] if encoder else 0

    await websocket.send_json({
        "type": "config",
        "codec": codec,
        "width": width,
        "height": height,
        "description": (sps + pps).hex(),  # Annex B 格式的 SPS+PPS
    })


async def _handle_input(device_id: str, data: dict[str, Any], stream_service: StreamService) -> None:
    """
    处理来自客户端的输入事件。

    通过 ScrcpyEncoder.send_input() 发送二进制控制消息（scrcpy 协议），
    延迟 <5ms。如果控制 socket 不可用，自动回退到 adb shell input。

    支持的 action：
        - touch: 触摸事件（x, y）
        - swipe: 滑动事件（x1, y1, x2, y2, duration）
        - key: 按键事件（keycode）
        - text: 文本输入（text）

    另外支持客户端 1Hz 帧率上报（用于自适应码率决策）：
        { "op": "stats", "fps": 25 }
    """
    if data.get("op") == "stats":
        try:
            fps = float(data.get("fps", 0))
        except (TypeError, ValueError):
            fps = 0.0
        stream_service.report_client_fps(device_id, fps)
        return

    logger.info("input_received", device=device_id, action=data.get("action"), data=data)

    encoder = stream_service.get_encoder(device_id)
    if encoder is None:
        logger.warning("no_encoder_for_input", device=device_id)
        return

    logger.info("sending_input_via_encoder", device=device_id,
                resolution=encoder.resolution,
                has_control_sender=encoder._control_sender is not None)
    await encoder.send_input(data)
    logger.info("input_sent", device=device_id)
