"""
截图 PNG 旋转补偿（方案 38 B4）
============================

层：应用层（纯函数）。

screencap 输出物理 framebuffer 方向，不随 Android 显示 rotation 旋转；
rotation（Surface 值 0-3）> 0 时对 PNG 做同向补偿，使截图与视频流一致。

补偿方向＝按 rotation 顺时针 r×90°（映射到 PIL 逆时针转置常量）：
    1 → ROTATE_270（逆时针 270 ≡ 顺时针 90）
    2 → ROTATE_180（180° 已真机验证于 .24；方向自反无歧义）
    3 → ROTATE_90（逆时针 90 ≡ 顺时针 270）
90/270 尚无真机样本，方向属已知限制（方案 38 §七）。
"""

import io

from PIL import Image

_ROTATION_TRANSPOSE = {
    1: Image.Transpose.ROTATE_270,
    2: Image.Transpose.ROTATE_180,
    3: Image.Transpose.ROTATE_90,
}


def rotate_png_payload(data: bytes, rotation: int) -> bytes:
    """
    按 rotation 补偿旋转 PNG 数据。

    参数：
        data: PNG 图像字节。
        rotation: Surface rotation 值（0-3）。

    返回：
        补偿后的 PNG 字节；rotation 为 0 或越界时字节级原样透传。
    """
    if rotation not in _ROTATION_TRANSPOSE:
        return data
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        rotated = img.transpose(_ROTATION_TRANSPOSE[rotation])
        out = io.BytesIO()
        rotated.save(out, format="PNG")
        return out.getvalue()