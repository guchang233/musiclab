#!/usr/bin/env python3
"""生成应用图标（纯标准库）：icon.png / icon.ico / 32x32.png / 128x128.png。

.ico 采用 Vista+ 支持的 PNG 内嵌格式，无需编码转换库。
"""
import struct
import zlib
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src-tauri", "icons")
SIZE = 512

# 简单图形：深色圆角底 + 亮色波形 + 音符点
BG = (24, 26, 38)
ACCENT = (94, 234, 212)
WAVE = (255, 184, 108)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def pixel(x, y):
    cx = cy = (SIZE - 1) / 2
    r = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    if r > SIZE * 0.48:  # 圆角外
        return (0, 0, 0, 0)
    base = lerp(BG, (34, 40, 60), r / (SIZE * 0.48))
    # 波形：正弦多条
    import math
    t = x / SIZE
    amp = SIZE * 0.22
    wave_y = SIZE * 0.5 + math.sin(t * 2 * math.pi * 3) * amp
    d = abs(y - wave_y)
    if d < 6:
        return (*WAVE, 255)
    # 音符点
    for dx, dy, rr in [(0.32, 0.30, 0.055), (0.68, 0.62, 0.055)]:
        px, py, pr = dx * SIZE, dy * SIZE, rr * SIZE
        if (x - px) ** 2 + (y - py) ** 2 < pr * pr:
            return (*ACCENT, 255)
    return (*base, 255)


def make_png(size):
    rows = []
    scale = SIZE / size
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            r, g, b, a = pixel(int(x * scale), int(y * scale))
            row += bytes((r, g, b, a))
        rows.append(bytes(row))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


def chunk(tag, data):
    c = tag + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)


os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "icon.png"), "wb") as f:
    f.write(make_png(512))
with open(os.path.join(OUT, "32x32.png"), "wb") as f:
    f.write(make_png(32))
with open(os.path.join(OUT, "128x128.png"), "wb") as f:
    f.write(make_png(128))
with open(os.path.join(OUT, "128x128@2x.png"), "wb") as f:
    f.write(make_png(256))

# ICO：PNG 内嵌（256 及以下尺寸均可，Vista+ 支持）
png = make_png(256)
ico = struct.pack("<HHH", 0, 1, 1)
ico += struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 6 + 16)
ico += png
with open(os.path.join(OUT, "icon.ico"), "wb") as f:
    f.write(ico)
print("icons written to", OUT)
