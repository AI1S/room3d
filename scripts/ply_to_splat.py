#!/usr/bin/env python
"""3D 高斯泼溅 PLY -> .splat（每点 32 字节）转换器。

为什么需要它：Postshot / Luma / nerfstudio 导出的都是 PLY（几十到几百 MB），
Spark 虽然能直接读 PLY，但 .splat 体积只有 1/2~1/3 且解析更快，适合网页分发。

.splat 每点 32 字节，小端：
  float32 x, y, z          位置
  float32 sx, sy, sz       尺度（这里存线性 sigma，不是 log 尺度）
  uint8   r, g, b, a       颜色 + 不透明度（a = sigmoid(opacity) * 255）
  uint8   q0, q1, q2, q3   四元数，clip(128 + q * 128)，默认顺序 w,x,y,z

PLY 里 3DGS 的属性是"训练参数"形式，转换公式：
  color = clip(0.5 + 0.28209479177387814 * f_dc_i, 0, 1) * 255   （SH 0 阶）
  alpha = sigmoid(opacity) * 255
  scale = exp(scale_i)
  rot   = normalize(rot_0..3)

用法：
  py scripts/ply_to_splat.py 输入.ply 输出.splat
  py scripts/ply_to_splat.py 输入.ply 输出.splat --max-points 800000 --opacity-min 0.01
  py scripts/ply_to_splat.py 输入.ply 输出.splat --quat-order xyzw   # 表面出现梳状/条纹时试这个
  py scripts/ply_to_splat.py 输入.ply 输出.splat --up z-to-y         # COLMAP/3DGS 的 Z 朝上转成 Y 朝上

只依赖标准库；装了 numpy 会快很多（100 万点 numpy: 几秒 / 纯 python: 一两分钟）。
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import time

# Windows 控制台默认 GBK，中文提示在 MSYS bash 里会变成乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SH_C0 = 0.28209479177387814

# PLY 标量类型 -> struct 字符 / numpy dtype / 字节数
PLY_TYPES = {
    "char": ("b", 1), "int8": ("b", 1),
    "uchar": ("B", 1), "uint8": ("B", 1),
    "short": ("h", 2), "int16": ("h", 2),
    "ushort": ("H", 2), "uint16": ("H", 2),
    "int": ("i", 4), "int32": ("i", 4),
    "uint": ("I", 4), "uint32": ("I", 4),
    "float": ("f", 4), "float32": ("f", 4),
    "double": ("d", 8), "float64": ("d", 8),
}


class PlyError(Exception):
    pass


def read_ply_header(path):
    """返回 (format, vertex_count, properties, body_offset)"""
    with open(path, "rb") as f:
        header_bytes = b""
        while True:
            line = f.readline()
            if not line:
                raise PlyError("PLY 头不完整（没读到 end_header）")
            header_bytes += line
            if line.strip() == b"end_header":
                break
        body_offset = f.tell()

    text = header_bytes.decode("ascii", "replace")
    fmt = None
    count = 0
    props = []          # [(name, ply_type), ...] 只关心 vertex 元素
    current = None
    for raw in text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        key = parts[0]
        if key == "format":
            fmt = parts[1]
        elif key == "element":
            current = parts[1]
            if current == "vertex":
                count = int(parts[2])
        elif key == "property" and current == "vertex":
            if parts[1] == "list":
                raise PlyError("vertex 元素里出现 list 属性，暂不支持（这不是 3DGS 的 PLY？）")
            props.append((parts[2], parts[1]))
        elif key == "end_header":
            break
    return fmt, count, props, body_offset


def pick_props(props):
    """在 vertex 属性里找出我们需要的那些，返回 name -> (offset, struct char, size)。"""
    if not props:
        raise PlyError("PLY 里没有 vertex 属性")
    layout = {}
    offset = 0
    for name, ptype in props:
        if ptype not in PLY_TYPES:
            raise PlyError("不认识的 PLY 属性类型：%s (%s)" % (name, ptype))
        char, size = PLY_TYPES[ptype]
        layout[name] = (offset, char, size)
        offset += size
    return layout, offset


def has_any(layout, names):
    return all(n in layout for n in names)


def load_vertex_data(path):
    fmt, count, props, offset = read_ply_header(path)
    if count <= 0:
        raise PlyError("PLY 里没有任何顶点")
    layout, stride = pick_props(props)
    try:
        import numpy as np
    except Exception:
        np = None

    if fmt and fmt.startswith("binary_little_endian"):
        if np is not None:
            return _load_binary_numpy(path, offset, count, props, layout, stride, "<", np)
        return _load_binary_struct(path, offset, count, props, layout, stride, "<")
    if fmt and fmt.startswith("binary_big_endian"):
        if np is not None:
            return _load_binary_numpy(path, offset, count, props, layout, stride, ">", np)
        return _load_binary_struct(path, offset, count, props, layout, stride, ">")
    if fmt and fmt.startswith("ascii"):
        return _load_ascii(path, offset, count, props)
    raise PlyError("不支持的 PLY 格式：%r" % (fmt,))


def _load_binary_numpy(path, offset, count, props, layout, stride, endian, np):
    np_map = {"b": "i1", "B": "u1", "h": "i2", "H": "u2", "i": "i4", "I": "u4", "f": "f4", "d": "f8"}
    is_little = sys.byteorder == "little"
    use_little = (endian == "<") == is_little
    dtype = np.dtype([(name, np_map[layout[name][1]]) for name, _ in props], align=False)
    if endian == ">" and is_little:
        dtype = dtype.newbyteorder(">")
    elif endian == "<" and not is_little:
        dtype = dtype.newbyteorder("<")
    with open(path, "rb") as f:
        f.seek(offset)
        blob = f.read(count * stride)
    if len(blob) < count * stride:
        raise PlyError("PLY 数据被截断：期望 %d 字节，实际 %d" % (count * stride, len(blob)))
    arr = np.frombuffer(blob, dtype=dtype, count=count)
    data = {}
    for name in layout:
        col = arr[name]
        # 统一成全平台可用的 float32（nan/inf 留到后面清理）
        data[name] = col.astype(np.float32, copy=True) if col.dtype.kind in "iuf" else col
    return count, data


def _load_binary_struct(path, offset, count, props, layout, stride, endian):
    fmt = endian + "".join(layout[name][1] for name, _ in props)
    unpack = struct.Struct(fmt).unpack_from
    data = {name: [0.0] * count for name in layout}
    with open(path, "rb") as f:
        f.seek(offset)
        blob = f.read(count * stride)
        if len(blob) < count * stride:
            raise PlyError("PLY 数据被截断：期望 %d 字节，实际 %d" % (count * stride, len(blob)))
        pos = 0
        for i in range(count):
            vals = unpack(blob, pos)
            pos += stride
            for (name, _), v in zip(props, vals):
                data[name][i] = float(v)
    return count, data


def _load_ascii(path, offset, count, props):
    data = {name: [0.0] * count for name, _ in props}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        i = 0
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if len(parts) < len(props):
                continue
            for (name, _), v in zip(props, parts):
                data[name][i] = float(v)
            i += 1
            if i >= count:
                break
    if i < count:
        raise PlyError("ASCII PLY 顶点数不足：期望 %d，实际 %d" % (count, i))
    return count, data


def sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def save_splat(path, positions, scales, colors, quats, np):
    """positions/scales: (N,3) float; colors: (N,4) uint8; quats: (N,4) uint8 已编码"""
    out_dtype = [
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
        ("sx", "<f4"), ("sy", "<f4"), ("sz", "<f4"),
        ("r", "u1"), ("g", "u1"), ("b", "u1"), ("a", "u1"),
        ("q0", "u1"), ("q1", "u1"), ("q2", "u1"), ("q3", "u1"),
    ]
    if np is not None:
        n = positions.shape[0]
        rec = np.empty(n, dtype=np.dtype(out_dtype, align=False))
        rec["x"], rec["y"], rec["z"] = positions[:, 0], positions[:, 1], positions[:, 2]
        rec["sx"], rec["sy"], rec["sz"] = scales[:, 0], scales[:, 1], scales[:, 2]
        rec["r"], rec["g"], rec["b"], rec["a"] = colors[:, 0], colors[:, 1], colors[:, 2], colors[:, 3]
        rec["q0"], rec["q1"], rec["q2"], rec["q3"] = quats[:, 0], quats[:, 1], quats[:, 2], quats[:, 3]
        blob = rec.tobytes()
    else:
        pack = struct.Struct("<12f4B4B")
        chunk = []
        n = len(positions)
        for i in range(n):
            chunk.append(pack.pack(
                positions[i][0], positions[i][1], positions[i][2],
                scales[i][0], scales[i][1], scales[i][2],
                colors[i][0], colors[i][1], colors[i][2], colors[i][3],
                quats[i][0], quats[i][1], quats[i][2], quats[i][3],
            ))
        blob = b"".join(chunk)
    with open(path, "wb") as f:
        f.write(blob)
    return n


def convert(args):
    t0 = time.time()
    try:
        import numpy as np
    except Exception:
        np = None
    print("[ply_to_splat] numpy 可用" if np is not None else "[ply_to_splat] 没有 numpy，用纯 python（慢）")

    count, data = load_vertex_data(args.input)
    print("[ply_to_splat] 读到 %d 个高斯点" % count)
    names = set(data)

    def col(name):
        return data[name]

    # ---- 位置 ----
    if not has_any(names, ("x", "y", "z")):
        raise PlyError("PLY 缺少 x/y/z，看起来不是点云或高斯 PLY")
    if np is not None:
        pos = np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float32)
    else:
        pos = [[data["x"][i], data["y"][i], data["z"][i]] for i in range(count)]

    # ---- 尺度：3DGS 存的是 log 尺度 ----
    has_scale = has_any(names, ("scale_0", "scale_1", "scale_2"))
    if np is not None:
        if has_scale:
            sc = np.exp(np.stack([data["scale_0"], data["scale_1"], data["scale_2"]], axis=1))
            sc = np.clip(np.nan_to_num(sc, nan=args.default_scale, posinf=args.default_scale,
                                       neginf=args.default_scale), 1e-6, 10.0)
        else:
            sc = np.full((count, 3), args.default_scale, dtype=np.float32)
        sc = sc.astype(np.float32)
    else:
        sc = [[0.0, 0.0, 0.0] for _ in range(count)]
        for i in range(count):
            for j in range(3):
                if has_scale:
                    v = math.exp(data["scale_%d" % j][i])
                    if not math.isfinite(v) or v <= 0:
                        v = args.default_scale
                    sc[i][j] = min(max(v, 1e-6), 10.0)
                else:
                    sc[i][j] = args.default_scale

    # ---- 颜色：SH 0 阶 f_dc，或者普通点云的 red/green/blue ----
    has_dc = has_any(names, ("f_dc_0", "f_dc_1", "f_dc_2"))
    has_rgb = has_any(names, ("red", "green", "blue"))
    if np is not None:
        if has_dc:
            rgb = 0.5 + SH_C0 * np.stack([data["f_dc_0"], data["f_dc_1"], data["f_dc_2"]], axis=1)
            rgb = np.clip(rgb, 0.0, 1.0)
        elif has_rgb:
            rgb = np.stack([data["red"], data["green"], data["blue"]], axis=1).astype(np.float32)
            if float(np.max(rgb)) > 1.5:
                rgb = rgb / 255.0
            rgb = np.clip(rgb, 0.0, 1.0)
        else:
            print("[ply_to_splat] 警告：没有颜色属性，全部涂成中灰")
            rgb = np.full((count, 3), 0.6, dtype=np.float32)

        # ---- 不透明度：sigmoid ----
        if "opacity" in names:
            op = 1.0 / (1.0 + np.exp(-np.nan_to_num(data["opacity"], nan=-10.0)))
        elif "a" in names or "alpha" in names:
            op = data["a"] if "a" in names else data["alpha"]
            op = np.clip(op / 255.0 if float(np.max(op)) > 1.5 else op, 0.0, 1.0)
        else:
            op = np.ones(count, dtype=np.float32)

        alpha = np.clip(op, 0.0, 1.0) * 255.0
        colors = np.empty((count, 4), dtype=np.uint8)
        colors[:, :3] = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
        colors[:, 3] = np.clip(alpha + 0.5, 0, 255).astype(np.uint8)

        # ---- 四元数 ----
        has_rot = has_any(names, ("rot_0", "rot_1", "rot_2", "rot_3"))
        if has_rot:
            q = np.stack([data["rot_0"], data["rot_1"], data["rot_2"], data["rot_3"]], axis=1).astype(np.float64)
            norm = np.linalg.norm(q, axis=1, keepdims=True)
            norm[norm < 1e-12] = 1.0
            q = q / norm
        else:
            q = np.zeros((count, 4), dtype=np.float64)
            q[:, 0] = 1.0
        if args.quat_order == "xyzw":
            q = q[:, [3, 0, 1, 2]]
        quats = np.clip(q * 128.0 + 128.0 + 0.5, 0, 255).astype(np.uint8)

        # ---- 剪枝 / 降采样 ----
        keep = np.ones(count, dtype=bool)
        if args.opacity_min > 0:
            keep &= op >= args.opacity_min
        keep &= np.isfinite(pos).all(axis=1)
        # 去掉离群点：真实重建偶尔会在房间外飘出大量噪声高斯，
        # 它们会把包围盒撑大，导致前端自动取景跑到房间外面去看一片雾。
        if args.trim > 0:
            lo = np.percentile(pos, args.trim, axis=0)
            hi = np.percentile(pos, 100.0 - args.trim, axis=0)
            inside = np.all((pos >= lo) & (pos <= hi), axis=1)
            trimmed = int((keep & ~inside).sum())
            if trimmed:
                print("[ply_to_splat] %s%% 分位裁掉 %d 个离群点" % (args.trim, trimmed))
            keep &= inside
        removed = int(count - int(keep.sum()))
        if removed:
            print("[ply_to_splat] 剪掉 %d 个点（透明度低/坐标非法）" % removed)
        pos, sc, colors, quats, op = pos[keep], sc[keep], colors[keep], quats[keep], op[keep]
        n = pos.shape[0]
        if args.max_points and n > args.max_points:
            idx = np.random.default_rng(args.seed).choice(n, size=args.max_points, replace=False)
            idx.sort()
            pos, sc, colors, quats = pos[idx], sc[idx], colors[idx], quats[idx]
            n = args.max_points
            print("[ply_to_splat] 随机降采样到 %d 个点" % n)
        if n == 0:
            raise PlyError("剪枝后一个点都不剩，把 --opacity-min 调小一点")

        # ---- 坐标轴 ----
        if args.up == "z-to-y":
            # Z 朝上 -> Y 朝上：(x, y, z) -> (x, z, -y)
            x, y, z = pos[:, 0].copy(), pos[:, 1].copy(), pos[:, 2].copy()
            pos = np.stack([x, z, -y], axis=1)
            sx, sy, sz = sc[:, 0].copy(), sc[:, 1].copy(), sc[:, 2].copy()
            sc = np.stack([sx, sz, sy], axis=1)
            q = (quats.astype(np.float32) - 128.0) / 128.0
            # 绕 X 轴 -90°: (w=cos(-45), x=sin(-45),0,0) * q
            c, s = math.cos(-math.pi / 4), math.sin(-math.pi / 4)
            qw, qx, qy, qz = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
            q2 = np.stack([
                c * qw - s * qx,
                c * qx + s * qw,
                c * qy - s * qz,
                c * qz + s * qy,
            ], axis=1)
            quats = np.clip(q2 * 128.0 + 128.0 + 0.5, 0, 255).astype(np.uint8)
    else:
        # ---- 纯 python 分支 ----
        colors = [[0, 0, 0, 0] for _ in range(count)]
        for i in range(count):
            if has_dc:
                rgb = [min(max(0.5 + SH_C0 * data["f_dc_%d" % j][i], 0.0), 1.0) for j in range(3)]
            elif has_rgb:
                rgb = [min(max(data[c][i] / 255.0, 0.0), 1.0) for c in ("red", "green", "blue")]
            else:
                rgb = [0.6, 0.6, 0.6]
            if "opacity" in names:
                op = sigmoid(data["opacity"][i])
            else:
                op = 1.0
            colors[i] = [int(rgb[0] * 255 + 0.5), int(rgb[1] * 255 + 0.5),
                         int(rgb[2] * 255 + 0.5), int(min(max(op, 0.0), 1.0) * 255 + 0.5)]
        has_rot = has_any(names, ("rot_0", "rot_1", "rot_2", "rot_3"))
        quats = []
        for i in range(count):
            if has_rot:
                q = [data["rot_%d" % j][i] for j in range(4)]
                norm = math.sqrt(sum(v * v for v in q)) or 1.0
                q = [v / norm for v in q]
            else:
                q = [1.0, 0.0, 0.0, 0.0]
            if args.quat_order == "xyzw":
                q = [q[3], q[0], q[1], q[2]]
            quats.append([min(max(int(v * 128 + 128 + 0.5), 0), 255) for v in q])
        keep = [i for i in range(count)
                if (colors[i][3] / 255.0) >= args.opacity_min
                and all(math.isfinite(pos[i][j]) for j in range(3))]
        pos = [pos[i] for i in keep]
        sc = [sc[i] for i in keep]
        colors = [colors[i] for i in keep]
        quats = [quats[i] for i in keep]
        if args.max_points and len(pos) > args.max_points:
            import random
            rng = random.Random(args.seed)
            idx = sorted(rng.sample(range(len(pos)), args.max_points))
            pos = [pos[i] for i in idx]
            sc = [sc[i] for i in idx]
            colors = [colors[i] for i in idx]
            quats = [quats[i] for i in idx]
        if not pos:
            raise PlyError("剪枝后一个点都不剩，把 --opacity-min 调小一点")

    n = save_splat(args.output, pos, sc, colors, quats, np)
    size = os.path.getsize(args.output)
    print("[ply_to_splat] 写出 %s：%d 点，%.2f MB（每点 32B），耗时 %.1fs"
          % (args.output, n, size / 1048576.0, time.time() - t0))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="3DGS PLY -> .splat（32B/点）")
    ap.add_argument("input", help="输入 PLY")
    ap.add_argument("output", help="输出 .splat")
    ap.add_argument("--max-points", type=int, default=0, help="随机降采样到最多这么多点（0=不限制）")
    ap.add_argument("--opacity-min", type=float, default=0.005, help="透明度剪枝阈值（默认 0.005）")
    ap.add_argument("--trim", type=float, default=0.0,
                    help="按分位裁掉四周离群点（0=不裁；重建有飘浮噪声时用 1~2，单位是百分比）")
    ap.add_argument("--quat-order", choices=["wxyz", "xyzw"], default="wxyz",
                    help="PLY 里四元数的分量顺序，默认 wxyz（3DGS 官方）；画面出现梳齿/条纹改成 xyzw")
    ap.add_argument("--up", choices=["keep", "z-to-y"], default="keep",
                    help="坐标轴处理：keep 原样，z-to-y 把 Z 朝上转成 Y 朝上")
    ap.add_argument("--default-scale", type=float, default=0.02,
                    help="PLY 没有 scale_* 时用的各向同性尺度（米）")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    if not os.path.exists(args.input):
        print("找不到输入文件：%s" % args.input, file=sys.stderr)
        return 2
    try:
        return convert(args)
    except PlyError as e:
        print("[ply_to_splat] 失败：%s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
