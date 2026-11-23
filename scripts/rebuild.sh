#!/usr/bin/env bash
# 本地一键重建：视频 → 抽帧 → COLMAP 位姿 → Brush 训练 → PLY → .splat → 发布
#
# 用法：
#   bash scripts/rebuild.sh <video> <id> [--title "宿舍"] [--fps 2] [--no-publish]
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

  echo >&2 "── COLMAP mapper（可能要 10–30 分钟）──"
  t0=$SECONDS
  "$COLMAP_BIN" mapper \
    --database_path "$dir/db.db" --image_path "$dir/images" --output_path "$dir/sparse" \
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

echo ""
echo "── Brush 训练（${STEPS} 步，sh_degree=$SH_DEGREE，上限 $MAX_SPLATS 点）──"
mkdir -p "$DSET/rebuild"
t0=$SECONDS
"$BRUSH_BIN" "$DSET" \
  --total-steps "$STEPS" --sh-degree "$SH_DEGREE" \
  --max-splats "$MAX_SPLATS" --max-frames "$MAX_FRAMES" \
  --export-path "$DSET/rebuild" --export-name scene.ply --export-every 999999 \
  2>&1 | tail -25
echo "  训练耗时 $((SECONDS-t0))s"

PLY="$DSET/rebuild/scene.ply"
[ -f "$PLY" ] || { echo "Brush 没有导出 $PLY（看上面的日志）"; exit 21; }
echo "导出 $(ls -lh "$PLY" | awk '${print $5}') : $PLY"

if [ "$PUBLISH" = "1" ]; then
  echo ""
  echo "── 转 .splat 并发布 ──"
  py "$ROOT/scripts/pipeline.py" --id "$ID" --title "$TITLE" --splat "$PLY" || exit 22
  echo ""
  echo "✅ 完成。本地预览：npm run dev 后打开 http://localhost:5173/#/scan/$ID"
else
  echo ""
  echo "已跳过发布（--no-publish）。手动发布："
  echo "  py scripts/pipeline.py --id $ID --title \"$TITLE\" --splat \"$PLY\""
fi
