# -*- coding: utf-8 -*-
"""
extract_frames.py  —  从手机视频抽帧，给 3DGS / COLMAP 用

依赖 ffmpeg（不在 PATH 上的话，用 --ffmpeg 指定完整路径）：
    winget install Gyan.FFmpeg
    https://www.gyan.dev/ffmpeg/builds/  （下载后把 bin 目录加进 PATH）

示例：
    py scripts/extract_frames.py --video D:/scan.mp4 --out data/scans/demo1/frames --fps 2
    py scripts/extract_frames.py --video scan.mp4 --out frames --fps 2 --every-ms 0 --max 400

为什么默认 fps 抽 2 帧/秒：
    1080p60 手拍 2 分钟 = 7200 帧，其中大量是重复帧（站着不动也会一直出帧）。
    2 fps 大约 240 帧，够 COLMAP / splatfacto 用，抽帧 1 分钟以内完成。
"""

import argparse
import os
import shutil
import subprocess
import sys


def find_ffmpeg(explicit):
    if explicit:
        return explicit
    for name in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def probe(ffmpeg, video):
    """尽量读出分辨率 / 时长，读不到就返回 None（不影响抽帧）。"""
    info = {}
    exe = ffmpeg.replace("ffmpeg.exe", "ffprobe.exe").replace("/ffmpeg", "/ffprobe")
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate:format=duration",
             "-of", "default=nw=1", video],
            capture_output=True, text=True, timeout=30,
        ).stdout
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.strip()] = v.strip()
    except Exception:
        pass
    return info


def main():
    ap = argparse.ArgumentParser(description="从视频抽帧（给高斯泼溅/COLMAP 用）")
    ap.add_argument("--video", required=True, help="输入视频")
    ap.add_argument("--out", required=True, help="输出帧目录")
    ap.add_argument("--fps", type=float, default=2.0, help="抽帧帧率，默认 2")
    ap.add_argument("--max", type=int, default=0, help="最多抽多少帧，0 = 不限")
    ap.add_argument("--width", type=int, default=0, help="缩放到指定宽度，0 = 原尺寸")
    ap.add_argument("--ffmpeg", default="", help="ffmpeg 完整路径")
    args = ap.parse_args()

    if not os.path.isfile(args.video):
        sys.exit("找不到视频文件：" + args.video)

    ffmpeg = find_ffmpeg(args.ffmpeg)
    if not ffmpeg:
        sys.exit(
            "PATH 里没有 ffmpeg。\n"
            "  1) winget install Gyan.FFmpeg   （然后重开终端）\n"
            "  2) 或到 https://www.gyan.dev/ffmpeg/builds/ 下载，用 --ffmpeg 指定 ffmpeg.exe 路径\n"
            "装好后重跑本脚本。"
        )

    if os.path.isdir(args.out):
        old = [f for f in os.listdir(args.out) if f.lower().endswith((".jpg", ".png"))]
        if old:
            print("注意：输出目录已有 %d 张图，先清空避免混入旧帧" % len(old))
            for f in old:
                os.remove(os.path.join(args.out, f))
    else:
        os.makedirs(args.out)

    info = probe(ffmpeg, args.video)
    if info:
        print("视频：%sx%s  时长约 %s 秒" % (info.get("width", "?"), info.get("height", "?"), info.get("duration", "?")))

    vf = ["fps=%s" % args.fps]
    if args.width > 0:
        vf.append("scale=%d:-1" % args.width)
    vf.append("eq=brightness=0.0:saturation=1.0")

    cmd = [ffmpeg, "-y", "-i", args.video, "-vf", ",".join(vf),
           "-vsync", "0", "-q:v", "2"]
    if args.max > 0:
        cmd += ["-frames:v", str(args.max)]
    cmd.append(os.path.join(args.out, "frame_%05d.jpg"))

    print("抽帧中…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        sys.exit("ffmpeg 失败，退出码 %d" % r.returncode)

    frames = sorted(f for f in os.listdir(args.out) if f.endswith(".jpg"))
    if not frames:
        sys.exit("没有抽出任何帧，检查视频路径 / 编码")
    total = sum(os.path.getsize(os.path.join(args.out, f)) for f in frames)
    print("完成：%d 帧，合计 %.1f MB -> %s" % (len(frames), total / 1024.0 / 1024.0, args.out))
    print("下一步（W2 会做成一条命令）：")
    print("  ns-train splatfacto --data <COLMAP 数据集目录>")
    print("  # 或用 Postshot / Luma 导入这些帧")


if __name__ == "__main__":
    main()
