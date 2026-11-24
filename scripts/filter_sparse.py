"""过滤 COLMAP sparse 模型里的离群 3D 点（Brush 用它初始化高斯，噪声点会把场景撑大）。

用法：py filter_sparse.py <sparse_dir> [--out <dir>] [--max-err 2.0] [--min-track 3] [--trim 0.0]
输出：<out>/points3D.bin 过滤后；<out>/images.bin 里被删点对应 point3D_id 置 -1；其余文件复制。
"""
import argparse, io, os, shutil, struct, sys

import numpy as np


def read_points3d(path):
    f = open(path, "rb")
    num = struct.unpack("<Q", f.read(8))[0]
    recs = []
    for _ in range(num):
        pid = struct.unpack("<Q", f.read(8))[0]
        xyz = struct.unpack("<ddd", f.read(24))
        rgb = f.read(3)
        err = struct.unpack("<d", f.read(8))[0]
        tl = struct.unpack("<Q", f.read(8))[0]
        track = f.read(tl * 8)
        recs.append((pid, xyz, rgb, err, tl, track))
    f.close()
    return recs


def write_points3d(path, recs):
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(recs)))
        for pid, xyz, rgb, err, tl, track in recs:
            f.write(struct.pack("<Q", pid))
            f.write(struct.pack("<ddd", *xyz))
            f.write(rgb)
            f.write(struct.pack("<d", err))
            f.write(struct.pack("<Q", tl))
            f.write(track)


def patch_images(path, removed):
    """把 images.bin 里指向被删点的 point2D 的 point3D_id 改成 -1（原地改字节）。"""
    buf = bytearray(open(path, "rb").read())
    f = io.BytesIO(bytes(buf))
    num = struct.unpack("<Q", f.read(8))[0]
    patched = 0
    for _ in range(num):
        f.read(8)                      # image_id
        f.read(32)                     # qvec
        f.read(24)                      # tvec
        f.read(4)                      # camera_id
        while True:                    # name
            c = f.read(1)
            if c in (b"\x00", b""):
                break
        npts_off = f.tell()
        npts = struct.unpack("<Q", f.read(8))[0]
        for _ in range(npts):
            f.read(16)                 # x, y
            off = f.tell()
            pid = struct.unpack("<q", f.read(8))[0]
            if pid in removed:
                buf[off:off + 8] = struct.pack("<q", -1)
                patched += 1
    open(path, "wb").write(bytes(buf))
    return patched


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sparse_dir")
    ap.add_argument("--out", default=None, help="输出目录（默认 <sparse_dir>_clean）")
    ap.add_argument("--max-err", type=float, default=2.0, help="最大重投影误差（像素）")
    ap.add_argument("--min-track", type=int, default=3, help="最小观测数（被几张图看到）")
    ap.add_argument("--trim", type=float, default=0.5, help="再按分位裁掉的空间离群点（百分比）")
    args = ap.parse_args(argv)

    src = args.sparse_dir
    out = args.out or (src.rstrip("/\\") + "_clean")
    os.makedirs(out, exist_ok=True)

    recs = read_points3d(os.path.join(src, "points3D.bin"))
    print("原始 3D 点：", len(recs))
    keep = [r for r in recs if r[3] <= args.max_err and r[4] >= args.min_track]
    print("误差 <= %.2fpx 且观测数 >= %d：%d" % (args.max_err, args.min_track, len(keep)))
    if not keep:
        print("过滤后一个点都没了，把 --max-err 放宽一点", file=sys.stderr)
        return 2
    P = np.array([r[1] for r in keep])
    if args.trim > 0:
        lo = np.percentile(P, args.trim, axis=0)
        hi = np.percentile(P, 100 - args.trim, axis=0)
        keep = [r for r in keep if np.all(np.array(r[1]) >= lo) and np.all(np.array(r[1]) <= hi)]
        print("%.1f%% 分位裁掉空间离群点后：%d" % (args.trim, len(keep)))
    P = np.array([r[1] for r in keep])
    print("裁剪后 bbox x %.2f..%.2f y %.2f..%.2f z %.2f..%.2f" % (
        P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max(), P[:, 2].min(), P[:, 2].max()))

    kept_ids = {r[0] for r in keep}
    removed = {r[0] for r in recs} - kept_ids
    for name in ("cameras.bin", "rigs.bin", "frames.bin", "project.ini"):
        p = os.path.join(src, name)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(out, name))
    write_points3d(os.path.join(out, "points3D.bin"), keep)
    shutil.copy(os.path.join(src, "images.bin"), os.path.join(out, "images.bin"))
    n = patch_images(os.path.join(out, "images.bin"), removed)
    print("images.bin 里把 %d 个 point2D 的引用置为 -1" % n)
    print("输出：", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
