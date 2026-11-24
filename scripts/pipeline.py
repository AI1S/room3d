#!/usr/bin/env python
"""W2 一键管线：视频/重建产物 -> 发布到网页扫描库。

一条命令把「拍好的视频」推到「网页上能看的房源」：

    py scripts/pipeline.py --video D:/dorm.mp4 --id dorm --title 宿舍

它做四件事：
  1. 抽帧（ffmpeg）到 data/scans/<id>/frames/          —— 没有 ffmpeg 就提示怎么装
  2. 找重建产物：--splat 指定 / data/scans/<id>/rebuild/ 里丢的 PLY / --rebuild-cmd 跑命令
  3. 转成网页用的 .splat（32B/点，自动剪枝 + 可选降采样）
  4. 发布到 public/scans/<id>/（scene.splat / scene.spz + meta.json），并重建 public/scans/index.json

也可以只用后半段（Postshot 已经导出了 PLY 的时候）：

    py scripts/pipeline.py --id dorm --splat "C:/Users/liaos/Downloads/dorm.ply"

要补房源信息（面积/朝向/居室），直接编辑 public/scans/<id>/meta.json。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLY2SPLAT = os.path.join(ROOT, "scripts", "ply_to_splat.py")
DEFAULT_FAQ = [
    {
      "question": "这个房间多大？",
      "keywords": ["多大", "面积", "多少平", "几平"],
      "answer": "面积还没量：这套是刚发布的原始重建，面积/朝向还没填进 meta.json。\n先按 capture-sop 的参照物（A4 纸 / 标准门宽 0.9m）做尺度校正，再填面积。",
    },
    {
      "question": "朝向是哪边？",
      "keywords": ["朝向", "朝什么方向", "朝南", "朝北"],
      "answer": "朝向还没录：重建只还原几何和颜色，看不出东南西北。\n需要采集时记下手机指南针方向，或在 meta.json 里手动填。",
    },
  ]

DEFAULT_REFUSE = [
    {
        "keywords": ["柜子里面", "柜子内部", "衣柜里面", "衣柜内部", "抽屉", "里面有什么", "内部"],
        "answer": "这部分没有采集到，无法查看。\n镜头只拍到家具的外表面；柜子内部、床底、窗外这些地方没有画面，我们不会替你想象一个出来。",
    },
    {
        "keywords": ["承重", "墙体结构", "裂缝", "漏水", "水管", "电路", "墙里"],
        "answer": "这超出这次采集的数据范围（只有表面几何和颜色），无法判断。\n墙体结构、管线、裂缝这些要专业检测报告，平台不做推断。",
    },
    {
        "keywords": ["平方", "面积是", "产权"],
        "answer": "这里给的是重建出来的户型尺寸参考，不等于产权证面积。\n涉及交易的面积以产权证为准，可以在后台上传产权信息做对照。",
    },
]


def log(step, msg):
    print("[%s] %s" % (step, msg), flush=True)


def die(msg, code=1):
    print("\n[错误] " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


def human_size(path):
    try:
        b = os.path.getsize(path)
    except OSError:
        return "?"
    if b >= 1048576:
        return "%.2f MB" % (b / 1048576.0)
    return "%.0f KB" % (b / 1024.0)


def find_tool(name):
    return shutil.which(name)


# ---------------------------------------------------------------- 1. 抽帧
def probe_video(video):
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate:format=duration",
             "-of", "default=noprint_wrappers=1", video],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        info = {}
        for line in (out.stdout or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.strip()] = v.strip()
        return info
    except Exception:
        return None


def extract_frames(video, frames_dir, fps):
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        print("""
[提示] 这台电脑还没装 ffmpeg，抽不了帧。装一次就好（约 100MB）：

   winget install Gyan.FFmpeg

装完关掉这个终端重开一个（让 PATH 生效），再跑同一条命令。
或者：跳过抽帧——直接用 Postshot 打开视频，导出 .ply 后跑：
   py scripts/pipeline.py --id <房源id> --splat <导出的 ply>
""", flush=True)
        return False

    info = probe_video(video)
    if info:
        log("1/4", "视频 %s×%s，%s fps，时长 %s 秒" % (
            info.get("width", "?"), info.get("height", "?"), info.get("r_frame_rate", "?"),
            ("%.1f" % float(info["duration"])) if info.get("duration") not in (None, "N/A") else "?"))
    if os.path.isdir(frames_dir):
        old = [f for f in os.listdir(frames_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if old:
            log("1/4", "清掉上次抽的 %d 张帧" % len(old))
            for f in old:
                os.remove(os.path.join(frames_dir, f))
    os.makedirs(frames_dir, exist_ok=True)

    pattern = os.path.join(frames_dir, "frame_%05d.jpg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", video,
           "-vf", "fps=%s,scale='min(1600,iw)':-2" % fps, "-q:v", "2", pattern]
    log("1/4", "抽帧 fps=%s -> %s" % (fps, os.path.relpath(frames_dir, ROOT)))
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        print(res.stderr[-1500:], file=sys.stderr)
        die("ffmpeg 抽帧失败（日志见上）")
    n = len([f for f in os.listdir(frames_dir) if f.startswith("frame_")])
    log("1/4", "抽出 %d 帧，耗时 %.1fs" % (n, time.time() - t0))
    if n < 30:
        print("[警告] 帧数偏少（%d）。建议视频 60 秒以上、走动慢一点，否则重建容易糊。" % n, flush=True)
    return True


# ---------------------------------------------------------------- 2. 重建产物
def find_rebuild_input(scan_dir, explicit):
    if explicit:
        if not os.path.exists(explicit):
            die("--splat 指定的文件不存在：%s" % explicit)
        return explicit
    rebuild_dir = os.path.join(scan_dir, "rebuild")
    cands = []
    if os.path.isdir(rebuild_dir):
        for name in os.listdir(rebuild_dir):
            if name.lower().endswith((".ply", ".splat")):
                cands.append(os.path.join(rebuild_dir, name))
    if cands:
        cands.sort(key=os.path.getmtime, reverse=True)
        return cands[0]
    return None


def run_rebuild_cmd(template, frames_dir, out_path):
    cmd = template.replace("{frames}", frames_dir).replace("{out}", out_path)
    log("2/4", "跑重建命令：" + cmd)
    res = subprocess.run(cmd, shell=True, cwd=ROOT)
    if res.returncode != 0:
        die("重建命令失败（返回码 %d）" % res.returncode)
    if not os.path.exists(out_path):
        die("重建命令跑完了，但没找到产物：%s" % out_path)
    return out_path


# ---------------------------------------------------------------- 3. 转 .splat
def to_splat(src, out_splat, max_points, up, quat_order, trim=0.0, opacity_min=None):
    if src.lower().endswith(".splat"):
        log("3/4", "本身就是 .splat，直接复制")
        shutil.copyfile(src, out_splat)
        return
    cmd = [sys.executable, PLY2SPLAT, src, out_splat]
    if max_points:
        cmd += ["--max-points", str(max_points)]
    if up == "z-to-y":
        cmd += ["--up", "z-to-y"]
    if quat_order:
        cmd += ["--quat-order", quat_order]
    if trim:
        cmd += ["--trim", str(trim)]
    if opacity_min is not None:
        cmd += ["--opacity-min", str(opacity_min)]
    log("3/4", "PLY -> .splat（%s）" % human_size(src))
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        print(res.stdout[-2000:], res.stderr[-2000:], file=sys.stderr)
        die("PLY 转换失败")
    for line in (res.stdout or "").splitlines():
        if line.startswith("[ply_to_splat]"):
            print("      " + line, flush=True)


SPLAT_TRANSFORM = "@playcanvas/splat-transform@3.12.0"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from colmap_camera import camera_from_colmap, bounds_from_colmap  # noqa: E402


def npx_bin():
    for name in ("npx.cmd", "npx"):
        hit = shutil.which(name)
        if hit:
            return hit
    return None


def parse_points(text):
    m = re.search(r"([\d.]+)\s*([KM]?)\s+gaussians", text or "")
    if not m:
        return None
    mult = {"": 1, "K": 1000, "M": 1000000}[m.group(2)]
    return int(round(float(m.group(1)) * mult))


def to_format(src, out_path, fmt, max_points, up, quat_order, trim, opacity_min, filter_box=None):
    """产出发布文件。fmt=splat 走自带转换器；spz/sog 走 @playcanvas/splat-transform（压缩比高）。
    返回 (输出路径, 点数)。"""
    if fmt == "splat":
        to_splat(src, out_path, max_points, up, quat_order, trim, opacity_min)
        return out_path, (os.path.getsize(out_path) // 32)

    npx = npx_bin()
    if npx is None:
        print("[警告] 没找到 npx，装不了 splat-transform，退回 .splat", file=sys.stderr)
        to_splat(src, out_path, max_points, up, quat_order, trim, opacity_min)
        return out_path, (os.path.getsize(out_path) // 32)

    cmd = [npx, "--yes", SPLAT_TRANSFORM, "-w", src]
    if filter_box:
        cmd += ["-B", filter_box]
    if up == "z-to-y":
        cmd += ["-r", "-90,0,0"]
    if max_points:
        cmd += ["-d", "%d" % max_points]
    cmd.append(out_path)
    log("3/4", "PLY -> .%s（%s，压缩中）" % (fmt, human_size(src)))
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        print((res.stdout or "")[-1500:], (res.stderr or "")[-1500:], file=sys.stderr)
        die("压缩失败（%s）" % fmt)
    for line in (res.stdout or "").splitlines():
        if "gaussians" in line or "Writing" in line or line.strip().startswith("·"):
            print("      " + line.strip(), flush=True)
    points = parse_points(res.stdout) or parse_points(res.stderr)
    if not points and fmt == "spz":
        try:
            with open(out_path, "rb") as f:
                head = f.read(12)
            if len(head) == 12:
                points = struct.unpack("<III", head)[2]
        except Exception:
            points = None
    return out_path, points


# ---------------------------------------------------------------- 4. 发布
def make_meta(scan_id, title, asset_path, extra_note="", points=None, camera=None):
    if points is None:
        if asset_path.lower().endswith(".splat"):
            points = os.path.getsize(asset_path) // 32
        else:
            points = 0
    size_mb = os.path.getsize(asset_path) / 1048576.0
    note = "自动生成：面积 / 朝向 / 居室还没填，量完尺寸后改 meta.json。"
    if extra_note:
        note += " " + extra_note
    asset_name = os.path.basename(asset_path)
    meta = {
        "title": title,
        "captureNote": note,
        "metrics": [
            {"label": "面积", "value": "—", "big": True},
            {"label": "朝向", "value": "—"},
            {"label": "居室", "value": "—"},
            {"label": "重建点数", "value": "%s 万" % round(points / 10000.0, 1)},
        ],
        "scenes": [{
            "id": "scene",
            "name": "全屋",
            "title": title,
            "url": "scans/%s/%s" % (scan_id, asset_name),
        }],
        "build": {
            "points": points,
            "sizeMB": round(size_mb, 2),
            "format": os.path.splitext(asset_name)[1].lstrip("."),
            "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        },
        "faq": DEFAULT_FAQ,
        "refuse": DEFAULT_REFUSE,
        "unknown": [],
    }
    if camera:
        meta["camera"] = camera
    return meta


def publish(scan_id, title, asset_src, public_dir, keep_meta=False, points=None, camera=None):
    out_dir = os.path.join(public_dir, "scans", scan_id)
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, os.path.basename(asset_src))
    shutil.copyfile(asset_src, dst)
    # 换格式时清掉旧场景文件，免得浏览器继续吃缓存里的旧资产
    for stale in ("scene.splat", "scene.spz", "scene.sog", "scene.ply"):
        p = os.path.join(out_dir, stale)
        if os.path.isfile(p) and os.path.abspath(p) != os.path.abspath(dst):
            os.remove(p)
    log("4/4", "发布到 %s（%s）" % (os.path.relpath(out_dir, ROOT), human_size(dst)))

    meta_path = os.path.join(out_dir, "meta.json")
    if keep_meta and os.path.exists(meta_path):
        log("4/4", "保留已有的 meta.json（--keep-meta）")
    else:
        meta = make_meta(scan_id, title, dst, points=points, camera=camera)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        log("4/4", "写 meta.json（面积/朝向还是占位，记得补）")
    return dst, meta_path


def rebuild_index(public_dir):
    scans_dir = os.path.join(public_dir, "scans")
    entries = []
    if os.path.isdir(scans_dir):
        for name in sorted(os.listdir(scans_dir)):
            meta_path = os.path.join(scans_dir, name, "meta.json")
            if not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception as e:
                print("[警告] %s 读不了，跳过：%s" % (meta_path, e), flush=True)
                continue
            scenes = meta.get("scenes") or []
            entries.append({
                "id": name,
                "title": meta.get("title") or name,
                "metaUrl": "scans/%s/meta.json" % name,
                "sceneCount": len(scenes),
                "points": (meta.get("build") or {}).get("points"),
                "updated": datetime.fromtimestamp(
                    os.path.getmtime(meta_path)).astimezone().isoformat(timespec="seconds"),
            })
    index = {
        "generated": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "scans": entries,
    }
    out = os.path.join(scans_dir, "index.json")
    os.makedirs(scans_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print("[索引] scans/index.json：%d 套房源（%s）" % (
        len(entries), "、".join(e["title"] for e in entries)), flush=True)
    return index


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="视频/PLY -> 网页扫描库（W2 一键管线）")
    ap.add_argument("--video", help="手机拍的视频（mp4/mov）")
    ap.add_argument("--id", help="房源 id（英文/数字/-_），默认用视频文件名")
    ap.add_argument("--title", help="显示名，默认用 id")
    ap.add_argument("--fps", default="2", help="抽帧帧率（默认 2，60 秒视频约 120 帧）")
    ap.add_argument("--skip-frames", action="store_true", help="不抽帧，复用 data/scans/<id>/frames")
    ap.add_argument("--splat", help="直接用这个重建产物（.ply 或 .splat），跳过重建步骤")
    ap.add_argument("--rebuild-cmd", help="重建命令模板，可用 {frames} 和 {out}，（谨慎：会执行 shell）")
    ap.add_argument("--max-points", type=int, default=0, help="发布版最多多少高斯点（0=不限制）")
    ap.add_argument("--up", choices=["keep", "z-to-y"], default="keep", help="坐标轴修正，Z 朝上的扫描用 z-to-y")
    ap.add_argument("--quat-order", choices=["wxyz", "xyzw"], help="四元数顺序（画面出现梳齿/条纹时改成 xyzw）")
    ap.add_argument("--trim", type=float, default=0.0,
                    help="发布前按分位裁掉离群点（0=不裁；重建有飘浮噪声时用 1~2）")
    ap.add_argument("--opacity-min", type=float, default=None,
                    help="透明度剪枝阈值（默认 0.005，重建雾感重时提到 0.05~0.1）")
    ap.add_argument("--format", choices=["splat", "spz", "sog"], default="splat",
                    help="发布格式：splat（32B/点，无依赖）| spz（约 17B/点）| sog（约 14B/点，最省）")
    ap.add_argument("--camera-from", help="COLMAP 的 sparse/0 目录：自动算出网页默认机位写进 meta.camera")
    ap.add_argument("--filter-box", help="只保留这个盒子里的高斯：x,y,z,X,Y,Z（重建有飘浮离群点时用）")
    ap.add_argument("--crop-cameras", type=float, metavar="PAD",
                    help="按相机中心包围盒裁掉飘浮高斯，PAD 是外扩量（推荐 2.0；需配合 --camera-from）")
    ap.add_argument("--keep-meta", action="store_true", help="保留已有的 meta.json，只换 splat")
    ap.add_argument("--reindex", action="store_true", help="只重建 scans/index.json")
    ap.add_argument("--public-dir", default=os.path.join(ROOT, "public"))
    args = ap.parse_args(argv)

    if args.reindex:
        rebuild_index(args.public_dir)
        return 0

    if not args.video and not args.splat:
        ap.print_help()
        print("\n至少给一个 --video 或 --splat")
        return 2

    scan_id = args.id
    if not scan_id:
        if not args.video:
            die("没有 --id，也没有视频文件名可用")
        scan_id = os.path.splitext(os.path.basename(args.video))[0]
    scan_id = re.sub(r"[^0-9a-zA-Z_-]+", "-", scan_id).strip("-").lower()
    if not scan_id:
        die("房源 id 不合法")
    title = args.title or scan_id
    scan_dir = os.path.join(ROOT, "data", "scans", scan_id)
    frames_dir = os.path.join(scan_dir, "frames")
    asset_ext = "splat" if args.format == "splat" else args.format
    work_asset = os.path.join(scan_dir, "scene." + asset_ext)
    os.makedirs(scan_dir, exist_ok=True)

    print("房子 id：%s   显示名：%s\n" % (scan_id, title), flush=True)

    # 1. 抽帧
    frames_ok = os.path.isdir(frames_dir) and any(
        f.startswith("frame_") for f in os.listdir(frames_dir))
    if args.skip_frames:
        if not frames_ok:
            die("--skip-frames 要求 %s 里已有帧" % os.path.relpath(frames_dir, ROOT))
        log("1/4", "复用已有帧：%d 张" % len([f for f in os.listdir(frames_dir) if f.startswith("frame_")]))
    elif args.video:
        ok = extract_frames(args.video, frames_dir, args.fps)
        if not ok and not args.splat and not frames_ok:
            return 2
    else:
        log("1/4", "没给 --video，跳过抽帧")

    # 2. 重建产物
    src = find_rebuild_input(scan_dir, args.splat)
    if src is None and args.rebuild_cmd:
        out_path = os.path.join(scan_dir, "rebuild", "scene.ply")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        src = run_rebuild_cmd(args.rebuild_cmd, frames_dir, out_path)
    if src is None:
        print("""
[2/4] 还缺重建产物——这一步要 GPU 跑高斯泼溅，脚本不替你跑。二选一：

  A. Postshot（Windows 图形界面，最省事，推荐）
     1) 打开 Postshot -> New Project，导入视频或 frames 目录：
        %s
     2) 训练完成后 Export -> 选 PLY（不会调就选默认参数）
     3) 把导出的 ply 丢进：
        %s
     4) 再跑一次本命令（加 --skip-frames 可复用帧）：
        py scripts/pipeline.py --id %s --title %s --skip-frames

  B. 已经在别处导出了 ply：直接
        py scripts/pipeline.py --id %s --splat "<导出的 ply 路径>"

帧已经准备好了，重建完继续就行。
""" % (os.path.relpath(frames_dir, ROOT), os.path.relpath(os.path.join(scan_dir, "rebuild"), ROOT),
       scan_id, title, scan_id), flush=True)
        return 0

    log("2/4", "重建产物：%s（%s）" % (src, human_size(src)))

    # 3. 转成发布格式（先把裁剪盒算出来，to_format 要用）
    filter_box = args.filter_box
    if args.crop_cameras and args.camera_from:
        auto_box = bounds_from_colmap(args.camera_from, args.crop_cameras)
        if auto_box:
            log("3/4", "按相机包围盒裁剪（pad %s）：%s" % (args.crop_cameras, auto_box))
            filter_box = auto_box if not filter_box else filter_box
    _, points = to_format(src, work_asset, args.format, args.max_points, args.up,
                          args.quat_order, args.trim, args.opacity_min, filter_box)

    # 4. 发布 + 索引
    camera = camera_from_colmap(args.camera_from) if args.camera_from else None
    if camera:
        log("4/4", "默认机位：%s（fov %s°）" % (camera["position"], camera["fov"]))
    dst, meta_path = publish(scan_id, title, work_asset, args.public_dir,
                             keep_meta=args.keep_meta, points=points, camera=camera)
    index = rebuild_index(args.public_dir)

    print("""
──────── 完成 ────────
网页：npm run dev 然后打开 http://localhost:5173
      （手机同局域网预览：npm run dev -- --host）
房源：%s（%s）
补信息：%s
下一步：把面积/朝向/居室填进 meta.json，再把关键尺寸用卷尺或 A4 纸核一遍。
""" % (title, os.path.basename(dst), os.path.relpath(meta_path, ROOT)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
