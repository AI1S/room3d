# -*- coding: utf-8 -*-
"""
make_sample_splat.py  —  生成示例 .splat 场景（合成房间，无依赖）

用途：在拿到真实扫描之前，先把「载入 -> 渲染 -> 问答」这条链路跑通。
输出：
    public/scans/demo/scene.splat    主卧  4.2 x 3.4 x 2.8 m
    public/scans/demo/scene2.splat   次卧  3.2 x 3.0 x 2.8 m

.splat 格式：每点 32 字节
    3 x f32  position
    3 x f32  scale (线性 sigma，不是 log)
    4 x u8   RGBA
    4 x u8   四元数，编码 clip(128 + q * 128)，顺序 w, x, y, z（3DGS PLY 惯例）

用法：  py scripts/make_sample_splat.py
"""

import math
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "public", "scans", "demo"))

DENSITY = 2600          # 每平方米点数
SCALE_FLAT = (0.026, 0.026, 0.010)   # 墙面/地面：扁圆盘
SCALE_FURN = (0.030, 0.030, 0.014)
SCALE_ROUND = (0.024, 0.024, 0.024)


def clip8(v):
    n = int(round(128 + v * 128))
    return 0 if n < 0 else (255 if n > 255 else n)


def norm(v):
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / l, v[1] / l, v[2] / l)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def shade(base, light, jitter=0.025):
    k = light * random.uniform(1.0 - jitter, 1.0 + jitter)
    out = []
    for c in base:
        v = int(c * k)
        out.append(0 if v < 0 else (255 if v > 255 else v))
    return out


def sample_rect(pts, origin, u, v, area, rgb, lightfn, scale, skip=None, jitter=0.006):
    """在平面上撒点。origin + u*a + v*b，a,b in [0,1]。"""
    n = max(1, int(area * DENSITY))
    nrm = norm(cross(u, v))
    tint = []
    attempts = 0
    placed = 0
    budget = n * 40 + 400
    while placed < n and attempts < budget:
        attempts += 1
        a = random.random()
        b = random.random()
        x = origin[0] + u[0] * a + v[0] * b + nrm[0] * random.uniform(-jitter, jitter)
        y = origin[1] + u[1] * a + v[1] * b + nrm[1] * random.uniform(-jitter, jitter)
        z = origin[2] + u[2] * a + v[2] * b + nrm[2] * random.uniform(-jitter, jitter)
        if skip is not None and skip(x, y, z):
            continue
        px = x + nrm[0] * random.uniform(-jitter, jitter)
        py_ = y + nrm[1] * random.uniform(-jitter, jitter)
        pz = z + nrm[2] * random.uniform(-jitter, jitter)
        pts.append(((px, py_, pz), scale, shade(rgb, lightfn(px, py_, pz)), (0.0, 0.0, 0.0, 1.0)))
        placed += 1


def sample_box(pts, x0, x1, z0, z1, y0, y1, rgb_top, rgb_side, lightfn, scale=SCALE_FURN):
    """长方体家具：六个面都撒点（底面省略，看不见）。"""
    w = x1 - x0
    d = z1 - z0
    h = y1 - y0
    # 顶面
    sample_rect(pts, (x0, y1, z0), (w, 0, 0), (0, 0, d), w * d, rgb_top, lightfn, scale, jitter=0.004)
    # 四面侧壁
    sample_rect(pts, (x0, y0, z0), (w, 0, 0), (0, h, 0), w * h, rgb_side, lightfn, scale, jitter=0.004)
    sample_rect(pts, (x0, y0, z1), (w, 0, 0), (0, h, 0), w * h, rgb_side, lightfn, scale, jitter=0.004)
    sample_rect(pts, (x0, y0, z0), (0, 0, d), (0, h, 0), d * h, rgb_side, lightfn, scale, jitter=0.004)
    sample_rect(pts, (x1, y0, z0), (0, 0, d), (0, h, 0), d * h, rgb_side, lightfn, scale, jitter=0.004)


def build_room(w, d, h, wall_rgb, floor_rgb, ceil_rgb, window, door, furniture, seed):
    """window = (x0, x1, y0, y1) 在南墙(z=-d/2)；door = (z0, z1, y0, y1) 在东墙(x=+w/2)。"""
    random.seed(seed)
    pts = []
    hx = w / 2.0
    hz = d / 2.0
    wx0, wx1, wy0, wy1 = window
    dx0, dx1, dy0, dy1 = door

    def light(x, y, z):
        # 越靠近南窗越亮，越靠近天花板越亮，模拟一扇窗的天光
        dist = math.sqrt((x) ** 2 + (z + hz) ** 2)
        f = 1.06 - min(0.42, dist * 0.075)
        return f * (0.92 + 0.16 * (y / h))

    def in_window(x, y, z):
        return abs(z + hz) < 0.02 and wx0 < x < wx1 and wy0 < y < wy1

    def in_door(x, y, z):
        return abs(x - hx) < 0.02 and dx0 < z < dx1 and dy0 < y < dy1

    # 地面 / 天花板
    sample_rect(pts, (-hx, 0.0, -hz), (w, 0, 0), (0, 0, d), w * d, floor_rgb, light, SCALE_FLAT)
    sample_rect(pts, (-hx, h, -hz), (w, 0, 0), (0, 0, d), w * d, ceil_rgb, lambda x, y, z: 1.12, SCALE_FLAT)

    # 南墙（带窗）
    win_area = (wx1 - wx0) * (wy1 - wy0)
    sample_rect(pts, (-hx, 0.0, -hz), (w, 0, 0), (0, h, 0), w * h - win_area, wall_rgb, light, SCALE_FLAT, skip=in_window)
    # 北墙
    sample_rect(pts, (-hx, 0.0, hz), (w, 0, 0), (0, h, 0), w * h, wall_rgb, light, SCALE_FLAT)
    # 西墙
    sample_rect(pts, (-hx, 0.0, -hz), (0, 0, d), (0, h, 0), d * h, wall_rgb, light, SCALE_FLAT)
    # 东墙（带门洞）
    door_area = (dx1 - dx0) * (dy1 - dy0)
    sample_rect(pts, (hx, 0.0, -hz), (0, 0, d), (0, h, 0), d * h - door_area, wall_rgb, light, SCALE_FLAT, skip=in_door)

    # 窗玻璃：一整片高亮偏冷色的点，看起来是"外面很亮的白天"
    sample_rect(pts, (wx0, wy0, -hz - 0.01), (wx1 - wx0, 0, 0), (0, wy1 - wy0, 0),
                (wx1 - wx0) * (wy1 - wy0), (206, 224, 240),
                lambda x, y, z: 1.28, (0.030, 0.030, 0.006), jitter=0.002)
    # 窗框：窗洞四边的窄条（让窗户不像一个黑洞）
    frame = (196, 190, 178)
    sample_rect(pts, (wx0, wy1, -hz), (wx1 - wx0, 0, 0), (0, 0.06, 0), (wx1 - wx0) * 0.06, frame, lambda x, y, z: 1.2, SCALE_FLAT)
    sample_rect(pts, (wx0, wy0 - 0.06, -hz), (wx1 - wx0, 0, 0), (0, 0.06, 0), (wx1 - wx0) * 0.06, frame, lambda x, y, z: 1.05, SCALE_FLAT)
    sample_rect(pts, (wx0 - 0.06, wy0, -hz), (0.06, 0, 0), (0, wy1 - wy0, 0), (wy1 - wy0) * 0.06, frame, lambda x, y, z: 1.0, SCALE_FLAT)
    sample_rect(pts, (wx1, wy0, -hz), (0.06, 0, 0), (0, wy1 - wy0, 0), (wy1 - wy0) * 0.06, frame, lambda x, y, z: 1.0, SCALE_FLAT)

    for f in furniture:
        sample_box(pts, f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], light)
    return pts


def write_splat(path, pts):
    buf = bytearray()
    for pos, scale, rgb, quat in pts:
        buf += struct.pack("<3f", pos[0], pos[1], pos[2])
        buf += struct.pack("<3f", scale[0], scale[1], scale[2])
        buf += bytes([rgb[0], rgb[1], rgb[2], 255])
        qw, qx, qy, qz = quat
        buf += bytes([clip8(qw), clip8(qx), clip8(qy), clip8(qz)])
    with open(path, "wb") as fh:
        fh.write(bytes(buf))
    mb = len(buf) / 1024.0 / 1024.0
    print("  %-38s %7d 点  %6.2f MB" % (os.path.basename(path), len(pts), mb))


def main():
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    print("生成示例场景 -> " + OUT_DIR)

    # 主卧 4.2 x 3.4 x 2.8，南墙窗，东墙门；床贴西墙，衣柜贴北墙
    room1 = build_room(
        4.2, 3.4, 2.8,
        wall_rgb=(223, 220, 212),
        floor_rgb=(176, 143, 104),
        ceil_rgb=(240, 240, 236),
        window=(-0.8, 0.8, 0.9, 2.2),
        door=(0.4, 1.3, 0.0, 2.0),
        furniture=[
            (-2.05, -1.05, -0.55, 1.45, 0.0, 0.52, (232, 230, 226), (110, 128, 156)),
            (-2.05, -1.05, -0.62, -0.55, 0.0, 0.95, (210, 205, 196), (120, 108, 92)),
            (0.55, 2.05, -1.65, -1.05, 0.0, 2.05, (206, 188, 158), (188, 170, 142)),
        ],
        seed=11,
    )
    write_splat(os.path.join(OUT_DIR, "scene.splat"), room1)

    # 次卧 3.2 x 3.0 x 2.8
    room2 = build_room(
        3.2, 3.0, 2.8,
        wall_rgb=(228, 224, 216),
        floor_rgb=(168, 140, 108),
        ceil_rgb=(238, 238, 234),
        window=(-0.6, 0.6, 0.9, 2.1),
        door=(0.3, 1.2, 0.0, 2.0),
        furniture=[
            (-1.55, -0.55, -1.45, -0.15, 0.0, 0.5, (228, 226, 222), (96, 118, 148)),
            (1.55, 1.9, -1.4, 0.6, 0.0, 1.2, (200, 190, 172), (176, 166, 148)),
        ],
        seed=23,
    )
    write_splat(os.path.join(OUT_DIR, "scene2.splat"), room2)

    total = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in ("scene.splat", "scene2.splat"))
    print("合计 %.2f MB" % (total / 1024.0 / 1024.0))


if __name__ == "__main__":
    main()
