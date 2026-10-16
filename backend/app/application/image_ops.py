"""
截图 PNG 旋转补偿
============================

层：应用层（纯函数）。

仅补偿 rotation=2（180°）：

设备端 `screencap -p` 输出的是逻辑方向图像而非物理 framebuffer——.22
（rotation=1，竖屏面板横屏使用）实测原生输出已是 1280×800 横屏正立，
服务端再旋转 90° 反而过补偿出竖图（方案 38 B4 「输出物理方向」假设被
活体样本证伪）。h264 视频流帧天然正立、截图 raw 链前端仅对 rotation=2
做 180° 补偿，两条链对 90/270 均不旋转——PNG 链与其对齐（三链一致）。

rotation=2 时 framebuffer 整体倒置，180° 补偿方向自反无歧义
（已真机验证于 .24）；0/1/3 一律字节级原样透传。
"""

import io

from PIL import Image

_ROTATION_TRANSPOSE = {
    2: Image.Transpose.ROTATE_180,
}


def rotate_png_payload(data: bytes, rotation: int) -> bytes:
    """
    按 rotation 补偿旋转 PNG 数据。

    参数：
        data: PNG 图像字节。
        rotation: Surface rotation 值（0-3）。

    返回：
        补偿后的 PNG 字节；rotation 非 2（含 0/1/3 与越界）时字节级原样透传。
    """
    if rotation not in _ROTATION_TRANSPOSE:
        return data
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        rotated = img.transpose(_ROTATION_TRANSPOSE[rotation])
        out = io.BytesIO()
        rotated.save(out, format="PNG")
        return out.getvalue()