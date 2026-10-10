"""
截图 PNG 旋转补偿纯函数测试（方案 38 B4）
==========================================

测试 image_ops.rotate_png_payload：
    - rotation=0 / 越界 → 字节级原样透传（不增不减）
    - rotation=2 → 180° 像素位置互换锚点验证
    - rotation 1/2/3 → 两次互补旋转回原图（映射互补性守卫）
"""

import io

import pytest
from PIL import Image

from app.application.image_ops import rotate_png_payload


def _make_png(width: int = 3, height: int = 2) -> tuple[bytes, Image.Image]:
    """生成 width×height 逐像素异色 PNG（像素值编码坐标，供位置断言）。"""
    img = Image.new("RGB", (width, height))
    px = img.load()
    for x in range(width):
        for y in range(height):
            px[x, y] = (x * 80, y * 60, 40)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), img


def _pixels(data: bytes) -> list[tuple[int, int, int]]:
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        return list(img.getdata())


def test_rotation_zero_passthrough():
    """rotation=0 字节级原样（无 PIL 重编码，缩略图链路成本不变）。"""
    png, _ = _make_png()
    assert rotate_png_payload(png, 0) == png


def test_rotation_invalid_passthrough():
    """越界 rotation 原样透传（防御不猜）。"""
    png, _ = _make_png()
    assert rotate_png_payload(png, 7) == png


def test_rotation_180_flips_pixels():
    """rotation=2 → 180°：dst(x,y) == src(w-1-x, h-1-y)。"""
    png, src = _make_png()
    dst = _pixels(rotate_png_payload(png, 2))
    w, h = src.size
    src_px = src.load()
    for x in range(w):
        for y in range(h):
            assert dst[y * w + x] == src_px[w - 1 - x, h - 1 - y]


@pytest.mark.parametrize("rotation", [1, 2, 3])
def test_complementary_rotation_identity(rotation: int):
    """rotation r 后再旋转 4-r 回到原图（映射互补性守卫）。"""
    png, src = _make_png()
    once = rotate_png_payload(png, rotation)
    twice = rotate_png_payload(once, (4 - rotation) % 4)
    assert _pixels(twice) == list(src.getdata())