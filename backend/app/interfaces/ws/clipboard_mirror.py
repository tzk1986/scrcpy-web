"""
剪贴板单向镜像（设备 → PC）会话级轮询
========================================

层：接口 → WebSocket（方案 55，路线图第 25 项）。

浏览器焦点约束下设备复制自动同步 PC 剪贴板：WS 会话收到
``{action:'clipboard_mirror', enabled:true}`` 后起 1s 轮询 task，复用方案 54
已验收的 ``clipboard_get`` 拉取链（copy_key=0 纯读、不注入按键、
``clipboard_autosync=false`` 起流参数不动——上游 autosync 路线见方案 55 §2.1
否决依据）；文本变化经视频 WS 推 ``{type:'clipboard_push', text}``。

设计要点：
    - 会话级生命周期：实例随 video_stream 建立，断开会话 stop() 收口，不持久化。
    - 防环回：本会话 clipboard_set 成功后记一次性 suppressed_text，
      轮询读到同文不弹回（对齐上游 isSettingClipboard 语义）。
    - 失败自停：连续 max_failures 次拉取失败（编码器缺失/超时）→ 推
      ``{type:'clipboard_mirror', enabled:false, reason:'poll_failed'}``
      并自行收口，前端开关复位，防设备异常后空转。
    - 隐私：正文不入日志（只记长度，沿用方案 54 约束）；本模块从不读 PC 剪贴板。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.application.stream_service import StreamService
from app.core.logging import get_logger

logger = get_logger(__name__)

SendJson = Callable[[dict[str, Any]], Awaitable[None]]

# 方案 55 §3.2：1s 轮询 + 连续 10 次失败自停（单测可 monkeypatch）
DEFAULT_INTERVAL = 1.0
DEFAULT_MAX_FAILURES = 10


class ClipboardMirror:
    """设备→PC 单向镜像轮询 task（每 WS 会话一个实例）。"""

    def __init__(
        self,
        device_id: str,
        stream_service: StreamService,
        interval: float | None = None,
        max_failures: int | None = None,
    ) -> None:
        self._device_id = device_id
        self._stream_service = stream_service
        self._interval = DEFAULT_INTERVAL if interval is None else interval
        self._max_failures = (DEFAULT_MAX_FAILURES if max_failures is None
                              else max_failures)
        self._task: asyncio.Task[None] | None = None
        self._last_text: str | None = None
        self._suppressed_text: str | None = None

    @property
    def enabled(self) -> bool:
        return self._task is not None

    async def set_enabled(self, enabled: bool, send_json: SendJson) -> None:
        """幂等开/关：重复开启不重起 task，关闭收口在途 task。"""
        if enabled == self.enabled:
            return
        if not enabled:
            await self.stop()
            return
        self._last_text = None
        self._suppressed_text = None
        self._task = asyncio.create_task(self._poll_loop(send_json))
        logger.debug("clipboard_mirror_started", device=self._device_id,
                     interval=self._interval)

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def note_set(self, text: str) -> None:
        """本会话 PC→设备写入登记：下一次轮询读到同文则视为环回，不推送。"""
        if self.enabled:
            self._suppressed_text = text

    async def _poll_loop(self, send_json: SendJson) -> None:
        failures = 0
        while True:
            await asyncio.sleep(self._interval)
            encoder = self._stream_service.get_encoder(self._device_id)
            if encoder is None:
                text = None
            else:
                text = await encoder.get_device_clipboard(copy_key=0)
            if text is None:
                failures += 1
                if failures >= self._max_failures:
                    logger.warning("clipboard_mirror_poll_failed_stopped",
                                   device=self._device_id, failures=failures)
                    self._task = None
                    await self._safe_send(
                        send_json,
                        {"type": "clipboard_mirror", "enabled": False,
                         "reason": "poll_failed"})
                    return
                continue
            failures = 0
            if text == self._suppressed_text:
                # 环回抑制一次性生效（此后设备侧真实变更照常推送）
                self._suppressed_text = None
                continue
            if text != self._last_text:
                self._last_text = text
                logger.debug("clipboard_mirror_push", device=self._device_id,
                             text_len=len(text))
                if not await self._safe_send(
                        send_json, {"type": "clipboard_push", "text": text}):
                    return

    async def _safe_send(self, send_json: SendJson, msg: dict[str, Any]) -> bool:
        """发送失败=客户端已走，静默收口 task（会话 teardown 兜底 cancel）。"""
        try:
            await send_json(msg)
            return True
        except Exception as e:
            logger.debug("clipboard_mirror_send_failed",
                         device=self._device_id, error=str(e))
            self._task = None
            return False
