#!/usr/bin/env bash
# 本地一键重建：视频 → 抽帧 → COLMAP 位姿 → Brush 训练 → PLY → .splat → 发布
#
# 用法：
#   bash scripts/rebuild.sh <video> <id> [--title "宿舍"] [--fps 6] [--no-publish]
# 环境变量：STEPS / MAX_SPLATS / MAX_RESOLUTION / FORMAT(spz|splat) / SH_DEGREE
# 例：
#   bash scripts/rebuild.sh data/test2.mp4 dorm --title "宿舍 2 号床"
#
# 工具路径可用环境变量覆盖：FFMPEG、COLMAP_BIN、BRUSH_BIN
set -uo pipefail

VIDEO=""
ID=""
TITLE=""
FPS="${FPS:-2}"
STEPS="${STEPS:-15000}"
SH_DEGREE="${SH_DEGREE:-0}"
MAX_SPLATS="${MAX_SPLATS:-2000000}"
MAX_FRAMES="${MAX_FRAMES:-600}"
MAX_IMAGE_SIZE="${MAX_IMAGE_SIZE:-1600}"
MAX_RESOLUTION="${MAX_RESOLUTION:-1080}"
FORMAT="${FORMAT:-spz}"
PUBLISH=1

while [ $# -gt 0 ]; do
  case "$1" in
    --title) TITLE="$2"; shift 2 ;;
    --fps) FPS="$2"; shift 2 ;;
    --no-publish) PUBLISH=0; shift ;;
    --steps) STEPS="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) if [ -z "$VIDEO" ]; then VIDEO="$1"; elif [ -z "$ID" ]; then ID="$1"; else echo "多余参数：$1"; exit 2; fi; shift ;;
  esac
done

[ -n "$VIDEO" ] && [ -n "$ID" ] || { sed -n '2,12p' "$0"; exit 2; }
[ -f "$VIDEO" ] || { echo "找不到视频：$VIDEO"; exit 2; }
TITLE="${TITLE:-$ID}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

find_tool() {
  local name="$1"; shift
  if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return 0; fi
  local c
  for c in "$@"; do [ -x "$c" ] && { echo "$c"; return 0; }; done
  return 1
}

FFMPEG="${FFMPEG:-$(find_tool ffmpeg "C:/Users/liaos/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-9.1-full_build/bin/ffmpeg.exe" 2>/dev/null)}"
COLMAP_BIN="${COLMAP_BIN:-$(find_tool colmap "C:/Users/liaos/AI/tools/3dgs/colmap/bin/colmap.exe" 2>/dev/null)}"
BRUSH_BIN="${BRUSH_BIN:-$(find_tool brush_app "C:/Users/liaos/AI/tools/3dgs/brush/brush_app.exe" 2>/dev/null)}"

[ -n "$FFMPEG" ]     || { echo "缺少 ffmpeg：winget install Gyan.FFmpeg"; exit 12; }
[ -n "$COLMAP_BIN" ] || { echo "缺少 COLMAP：解压后设 COLMAP_BIN 环境变量"; exit 12; }
[ -n "$BRUSH_BIN" ]  || { echo "缺少 Brush：解压后设 BRUSH_BIN 环境变量"; exit 12; }

echo "ffmpeg : $FFMPEG"
echo "colmap : $COLMAP_BIN"
echo "brush  : $BRUSH_BIN"

DATASET="$ROOT/data/scans/$ID"
mkdir -p "$DATASET"

probe() {
  local bin_dir; bin_dir="$(dirname "$FFMPEG")"
  if [ -x "$bin_dir/ffprobe.exe" ]; then
    "$bin_dir/ffprobe.exe" -v error -select_streams v:0 -show_entries stream=width,height,nb_frames:format=duration -of default=nw=1 "$1" | tr '\n' ' '
  fi
}
echo "视频：$(probe "$VIDEO")"

# 建图；返回 0 且打印数据集目录
run_sfm() {
  local fps="$1"
  local dir="$DATASET/attempt_fps$fps"
  rm -rf "$dir"; mkdir -p "$dir/images" "$dir/sparse"

  echo >&2 "── 抽帧 ${fps}fps ──"
  local t0=$SECONDS
  "$FFMPEG" -v error -i "$VIDEO" -vf "fps=$fps,scale='min($MAX_IMAGE_SIZE,iw)':-2" -q:v 2 "$dir/images/f_%05d.jpg" || return 1
  local n; n=$(ls "$dir/images" | wc -l | tr -d ' ')
  echo >&2 "  帧数 $n（$((SECONDS-t0))s）"
  [ "$n" -ge 20 ] || { echo >&2 "  抽出的帧太少（$n）"; return 1; }

  echo >&2 "── COLMAP 特征提取 ──"
  t0=$SECONDS
  "$COLMAP_BIN" feature_extractor \
    --database_path "$dir/db.db" --image_path "$dir/images" \
    --ImageReader.single_camera 1 --ImageReader.camera_model OPENCV \
    --FeatureExtraction.use_gpu 1 --FeatureExtraction.max_image_size "$MAX_IMAGE_SIZE" \
    --SiftExtraction.max_num_features 16384 --SiftExtraction.peak_threshold 0.005 \
    >"$dir/log_features.txt" 2>&1 || { tail -5 "$dir/log_features.txt"; return 1; }
  echo >&2 "  $((SECONDS-t0))s"

  echo >&2 "── COLMAP 序列匹配 ──"
  t0=$SECONDS
  "$COLMAP_BIN" sequential_matcher \
    --database_path "$dir/db.db" \
    --SequentialMatching.overlap 10 --SequentialMatching.quadratic_overlap 1 \
    --FeatureMatching.use_gpu 1 --FeatureMatching.guided_matching 1 \
    >"$dir/log_match.txt" 2>&1 || { tail -5 "$dir/log_match.txt"; return 1; }
  echo >&2 "  $((SECONDS-t0))s"

  # 4.2 的默认参数在 1500 张图上会每 ~10% 帧做一次全局 BA，慢到几小时；
  # 放宽 BA 频率 + 降低内点门槛，先求「能注册」，离群点靠后面 filter_sparse.py 清。
  echo >&2 "── COLMAP mapper（约 1 秒/张，1500 张 ≈ 25 分钟）──"
  t0=$SECONDS
  "$COLMAP_BIN" mapper \
    --database_path "$dir/db.db" --image_path "$dir/images" --output_path "$dir/sparse" \
    --Mapper.ba_global_frames_ratio 5.0 --Mapper.ba_global_points_ratio 5.0 \
    --Mapper.ba_global_max_refinements 1 --Mapper.ba_global_max_num_iterations 30 \
    --Mapper.ba_local_max_num_iterations 10 \
    --Mapper.abs_pose_min_num_inliers 15 --Mapper.init_min_num_inliers 50 \
    >"$dir/log_mapper.txt" 2>&1 || { tail -5 "$dir/log_mapper.txt"; return 1; }
  echo >&2 "  $((SECONDS-t0))s"

  [ -d "$dir/sparse/0" ] || { echo >&2 "  mapper 没产出任何模型"; return 1; }

  local best="" best_sz=0 m sz
  for m in "$dir"/sparse/*/; do
    [ -f "$m/images.bin" ] || continue
    sz=$(stat -c %s "$m/images.bin" 2>/dev/null || echo 0)
    if [ "$sz" -gt "$best_sz" ]; then best_sz=$sz; best=$m; fi
  done
  [ -n "$best" ] || { echo >&2 "  sparse 里没有有效模型"; return 1; }
  echo >&2 "  最佳模型 $(basename "$best")（images.bin ${best_sz}B）"
  local reg
  reg=$("$COLMAP_BIN" model_analyzer --path "$best" 2>&1 | grep -i "registered images" | grep -oE '[0-9]+' | head -1)
  reg="${reg:-0}"
  echo >&2 "  注册 $reg / $n 张（$(( 100 * reg / n ))%）"
  if [ "$reg" -lt $(( n / 3 )) ]; then
    echo >&2 "  ⚠ 注册率低于 1/3：位姿不可靠"
    return 1
  fi
  echo "$dir"
  return 0
}

DSET=""
if DSET=$(run_sfm "$FPS"); then
  :
else
  NEXT_FPS=$(( FPS * 3 ))
  echo ""
  echo "══ 第一次建图失败，改用 ${NEXT_FPS}fps 重试 ══"
  FPS="$NEXT_FPS"
  DSET=$(run_sfm "$FPS") || { echo ""; echo "两轮都失败：帧间重叠不足，按 docs/capture-sop.md 重拍"; exit 20; }
fi

BEST=$(for d in "$DSET"/sparse/*/; do [ -f "$d/images.bin" ] && echo "$(stat -c %s "$d/images.bin") $d"; done | sort -rn | head -1 | cut -d' ' -f2-)
if [ "$(basename "$BEST")" != "0" ]; then
  echo "最佳模型是 sparse/$(basename "$BEST")，复制到 sparse/0 供 Brush 使用"
  rm -rf "$DSET/sparse/0"
  cp -r "$BEST" "$DSET/sparse/0"
fi

# Brush 用 COLMAP 的 3D 点初始化高斯；mapper 放宽参数后离群点很多，
# 不清掉会把场景撑到几倍大（网页里就变成一团雾），所以先过滤一遍。
echo ""
echo "── 过滤稀疏点（去掉离群/低观测 3D 点，Brush 初始化更干净）──"
if py "$ROOT/scripts/filter_sparse.py" "$DSET/sparse/0" --out "$DSET/sparse_clean" 2>&1 | sed 's/^/  /'; then
  rm -rf "$DSET/sparse/0"
  cp -r "$DSET/sparse_clean" "$DSET/sparse/0"
else
  echo "  ⚠ 过滤脚本失败，用原始 sparse 继续训练"
fi

# Brush 0.3.0 的 COLMAP 加载器会在挂载目录里"随便挑一个 points3D.bin"，
# 只要目录里有第二个 COLMAP 模型（sparse2/、attempt 目录残留…），就会拿 A 模型的相机
# 配 B 模型的初始点 —— 结果是训练出来的高斯整体漂到相机外面，网页里就变成一团雾。
# 所以训练只给它一个干净目录：bset/sparse/0 单模型 + images 目录联接（不复制，省空间）。
STAGE="$DSET/bset"
echo ""
echo "── 搭 Brush 干净数据目录（bset：单模型 + images 联接）──"
rm -rf "$STAGE"; mkdir -p "$STAGE/sparse"
cp -r "$DSET/sparse/0" "$STAGE/sparse/0"
if ! ( cd "$STAGE" && powershell -NoProfile -Command "New-Item -ItemType Junction -Path '$(cygpath -w "$STAGE/images")' -Target '$(cygpath -w "$DSET/images")' | Out-Null" ) >/dev/null 2>&1; then
  echo "  联接建不出来，退回直接复制图片（占空间但能跑）"
  cp -r "$DSET/images" "$STAGE/images"
fi
NPTS=$(py -c "import struct,sys;f=open(sys.argv[1],'rb');print(struct.unpack('<Q',f.read(8))[0])" "$STAGE/sparse/0/points3D.bin" 2>/dev/null)
echo "  初始点：${NPTS:-?} 个（只来自 sparse/0，不会混到别的模型）"

echo ""
echo "── Brush 训练（${STEPS} 步，sh_degree=$SH_DEGREE，上限 $MAX_SPLATS 点，分辨率 $MAX_RESOLUTION）──"
mkdir -p "$DSET/rebuild"
t0=$SECONDS
"$BRUSH_BIN" "$STAGE" \
  --total-steps "$STEPS" --sh-degree "$SH_DEGREE" \
  --max-splats "$MAX_SPLATS" --max-frames "$MAX_FRAMES" \
  --max-resolution "$MAX_RESOLUTION" \
  --export-path "$DSET/rebuild" --export-name scene.ply --export-every 999999 \
  2>&1 | tail -25
echo "  训练耗时 $((SECONDS-t0))s（Brush 的进度用 \\r 刷屏，重定向后日志是空的，看 GPU 占用就行）"

PLY="$DSET/rebuild/scene.ply"
[ -f "$PLY" ] || { echo "Brush 没有导出 $PLY（看上面的日志）"; exit 21; }
echo "导出 $(ls -lh "$PLY" | awk '{print $5}') : $PLY"

PUB_ARGS=(--id "$ID" --title "$TITLE" --splat "$PLY"
          --format "$FORMAT" --camera-from "$STAGE/sparse/0" --crop-cameras 2.0)
if [ "$PUBLISH" = "1" ]; then
  echo ""
  echo "── 转 .$FORMAT 并发布（自动带默认机位 + 相机包围盒裁剪）──"
  py "$ROOT/scripts/pipeline.py" "${PUB_ARGS[@]}" || exit 22
  echo ""
  echo "✅ 完成。本地预览：npm run dev 后打开 http://localhost:5173/#/scan/$ID"
else
  echo ""
  echo "已跳过发布（--no-publish）。手动发布："
  echo "  py scripts/pipeline.py --id $ID --title \"$TITLE\" --splat \"$PLY\" --format $FORMAT --camera-from \"$STAGE/sparse/0\" --crop-cameras 2.0"
fi
