# -*- coding: utf-8 -*-
"""
server/main.py  —  W1 最小后端

只做三件事：
    1) 接视频，抽帧（调 ffmpeg）
    2) 接重建好的 .splat，和 meta.json 一起放进 scans/<id>/
    3) 把 scans/ 目录静态挂在 /scans，前端直接读

启动：
    py -m pip install -r requirements.txt
    py -m uvicorn server.main:app --reload --port 8000

自检：
    curl http://127.0.0.1:8000/api/health
"""

import json
import os
import shutil
import subprocess
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "scans")
os.makedirs(DATA, exist_ok=True)

app = FastAPI(title="Room3D API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# 注意：不要挂 /scans，前端 public/scans 是演示静态资源，代理会打架。
# 后端产物统一走 /api/scans/<id>/<file>，前端用相对路径 api/scans/... 访问。

ALLOWED_VIDEO = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")
ALLOWED_SPLAT = (".ply", ".splat", ".ksplat", ".spz", ".sog")


def ffmpeg_path():
    return shutil.which("ffmpeg")


def scan_dir(scan_id):
    d = os.path.join(DATA, scan_id)
    if not os.path.isdir(d):
        raise HTTPException(404, "扫描不存在：" + scan_id)
    return d


def read_meta(scan_id):
    p = os.path.join(DATA, scan_id, "meta.json")
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"id": scan_id, "title": scan_id, "scenes": [], "metrics": [], "faq": []}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "ffmpeg": ffmpeg_path() or None,
        "dataDir": DATA,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/scans")
def list_scans():
    out = []
    for name in sorted(os.listdir(DATA)):
        d = os.path.join(DATA, name)
        if not os.path.isdir(d):
            continue
        meta = read_meta(name)
        out.append({
            "id": name,
            "title": meta.get("title", name),
            "scenes": len(meta.get("scenes", [])),
            "frames": len(os.listdir(os.path.join(d, "frames"))) if os.path.isdir(os.path.join(d, "frames")) else 0,
            "updated": os.path.getmtime(d),
        })
    return {"scans": out}


@app.get("/api/scans/{scan_id}")
def get_scan(scan_id):
    return read_meta(scan_id)


@app.post("/api/scans")
async def upload_video(
    file: UploadFile = File(...),
    fps: float = Form(2.0),
    title: str = Form(""),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_VIDEO:
        raise HTTPException(400, "只接受视频：%s" % ", ".join(ALLOWED_VIDEO))

    scan_id = time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
    d = os.path.join(DATA, scan_id)
    frames = os.path.join(d, "frames")
    os.makedirs(frames, exist_ok=True)

    video_path = os.path.join(d, "source" + ext)
    with open(video_path, "wb") as fh:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)

    result = {"id": scan_id, "video": os.path.basename(video_path), "fps": fps, "frames": 0, "ffmpeg": None}
    ff = ffmpeg_path()
    if not ff:
        result["error"] = "服务器没有 ffmpeg，视频已存下但没抽帧"
    else:
        result["ffmpeg"] = ff
        cmd = [ff, "-y", "-i", video_path, "-vf", "fps=%s" % fps, "-vsync", "0", "-q:v", "2",
               os.path.join(frames, "frame_%05d.jpg")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            result["error"] = "ffmpeg 失败：" + r.stderr[-400:]
        else:
            result["frames"] = len([f for f in os.listdir(frames) if f.endswith(".jpg")])

    meta = {
        "id": scan_id,
        "title": title or ("扫描 " + scan_id),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": [],
        "scenes": [],
        "faq": [],
        "unknown": [],
        "captureNote": "刚上传，等待重建（W2 会在这里接自动管线）。",
    }
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return result


@app.post("/api/scans/{scan_id}/asset")
async def upload_asset(scan_id: str, file: UploadFile = File(...), name: str = Form("主卧")):
    d = scan_dir(scan_id)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_SPLAT:
        raise HTTPException(400, "只接受：%s" % ", ".join(ALLOWED_SPLAT))

    fname = "scene" + ext
    path = os.path.join(d, fname)
    with open(path, "wb") as fh:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)

    meta = read_meta(scan_id)
    scenes = [s for s in meta.get("scenes", []) if s.get("url") != "scans/%s/%s" % (scan_id, fname)]
    scenes.append({
        "id": os.path.splitext(fname)[0],
        "name": name,
        "title": meta.get("title", scan_id) + " · " + name,
        "url": "scans/%s/%s" % (scan_id, fname),
        "bytes": os.path.getsize(path),
    })
    meta["scenes"] = scenes
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return {"ok": True, "url": "scans/%s/%s" % (scan_id, fname), "bytes": os.path.getsize(path)}


@app.get("/api/scans/{scan_id}/{filename}")
def get_asset(scan_id: str, filename: str):
    """读 scans/<id>/ 下的产物：scene.splat / scene2.splat / meta.json 等。"""
    d = scan_dir(scan_id)
    safe = os.path.basename(filename)
    path = os.path.join(d, safe)
    if not os.path.isfile(path):
        raise HTTPException(404, "文件不存在：" + safe)
    return FileResponse(path)


@app.get("/api/scans/{scan_id}/frames/{filename}")
def get_frame(scan_id: str, filename: str):
    d = scan_dir(scan_id)
    path = os.path.join(d, "frames", os.path.basename(filename))
    if not os.path.isfile(path):
        raise HTTPException(404, "帧不存在")
    return FileResponse(path)


@app.delete("/api/scans/{scan_id}")
def delete_scan(scan_id: str):
    d = scan_dir(scan_id)
    if os.path.basename(d) == "demo":
        raise HTTPException(400, "demo 不能删")
    shutil.rmtree(d)
    return {"ok": True}
