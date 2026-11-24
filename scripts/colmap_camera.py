# -*- coding: utf-8 -*-
"""从 COLMAP 的 sparse/0 里挑一个"看得最全"的机位，给网页当默认视角。"""
import math
import os
import struct


def _q2r(q):
    w, x, y, z = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def _center(q, t):
    R = _q2r(q)
    return [-sum(R[k][i] * t[k] for k in range(3)) for i in range(3)]


def read_cameras(sparse_dir):
    cams = {}
    path = os.path.join(sparse_dir, "cameras.bin")
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        for _ in range(n):
            cid, model, w, h = struct.unpack("<iiQQ", f.read(24))
            k = {0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8, 6: 12, 7: 5, 8: 4, 9: 5, 10: 12}.get(model, 4)
            cams[cid] = struct.unpack("<" + "d" * k, f.read(8 * k))
    return cams


def read_images(sparse_dir):
    imgs = {}
    path = os.path.join(sparse_dir, "images.bin")
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        for _ in range(n):
            struct.unpack("<i", f.read(4))
            q = struct.unpack("<4d", f.read(32))
            t = struct.unpack("<3d", f.read(24))
            cid = struct.unpack("<i", f.read(4))[0]
            name = b""
            while True:
                c = f.read(1)
                if c == b"\x00" or c == b"":
                    break
                name += c
            nobs = struct.unpack("<Q", f.read(8))[0]
            f.read(nobs * 24)
            imgs[name.decode("utf-8", "replace")] = (q, t, cid, nobs)
    return imgs


def camera_from_colmap(sparse_dir):
    """返回 meta.camera：{position, target, fov, note}；解析失败返回 None。"""
    try:
        cams = read_cameras(sparse_dir)
        imgs = read_images(sparse_dir)
        if not imgs:
            return None
        centers = {k: _center(v[0], v[1]) for k, v in imgs.items()}
        med = [sorted(p[i] for p in centers.values())[len(centers) // 2] for i in range(3)]
        nobs_sorted = sorted((v[3] for v in imgs.values()), reverse=True)
        thr = nobs_sorted[max(0, int(len(nobs_sorted) * 0.3) - 1)]
        cand = []
        for name, (q, t, cid, nobs) in imgs.items():
            if nobs < thr:
                continue
            c = centers[name]
            d = sum((c[i] - med[i]) ** 2 for i in range(3)) ** 0.5
            cand.append((d, name))
        if not cand:
            return None
        cand.sort()
        name = cand[0][1]
        q, t, cid, _ = imgs[name]
        R = _q2r(q)
        c = centers[name]
        fwd = [R[2][0], R[2][1], R[2][2]]
        norm = sum(v * v for v in fwd) ** 0.5 or 1.0
        fwd = [v / norm for v in fwd]
        prm = cams.get(cid) or ()
        fov = 60.0
        if len(prm) >= 4 and prm[3]:
            fov = 2 * math.degrees(math.atan(prm[1] / (2 * prm[3])))
        return {
            "position": [round(v, 3) for v in c],
            "target": [round(c[i] + fwd[i] * 2.0, 3) for i in range(3)],
            "fov": round(min(80.0, max(45.0, fov)), 1),
            "note": "默认机位取自 COLMAP 位姿 %s（观测数前 30%% 里离房间中心最近的一帧）" % name,
        }
    except Exception as e:
        print("[警告] 读 COLMAP 位姿失败，跳过默认机位：%s" % e, file=__import__("sys").stderr)
        return None


def bounds_from_colmap(sparse_dir, pad=2.0):
    """相机中心包围盒 + pad，返回 splat-transform 的 -B 参数（"x,y,z,X,Y,Z"）。
    3DGS 训练常在没被相机看到的方位长出飘浮高斯，裁掉就干净了。"""
    imgs = read_images(sparse_dir)
    if not imgs:
        return None
    cs = [_center(v[0], v[1]) for v in imgs.values()]
    lo = [min(c[i] for c in cs) - pad for i in range(3)]
    hi = [max(c[i] for c in cs) + pad for i in range(3)]
    return ",".join("%.3f" % v for v in lo + hi)


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(camera_from_colmap(sys.argv[1]), ensure_ascii=False, indent=2))
