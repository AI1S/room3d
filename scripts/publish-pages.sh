#!/usr/bin/env bash
# 把 dist/ 发布到 GitHub Pages（gh-pages 分支）
# 用法：npm run publish:pages   （或 bash scripts/publish-pages.sh）
# 说明：GitHub Pages 对 gh-pages 分支做静态托管，地址 https://ai1s.github.io/room3d/
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WT="$(cd "$REPO/.." && pwd)/_room3d_ghp"

cd "$REPO"
echo "[1/5] 构建"
npm run build

echo "[2/5] 准备 gh-pages worktree: $WT"
if [ ! -e "$WT/.git" ]; then
  git worktree prune
  git worktree add --detach "$WT"
  ( cd "$WT" && git checkout --orphan gh-pages )
else
  ( cd "$WT" && git checkout gh-pages )
fi

echo "[3/5] 同步构建产物"
( cd "$WT"
  find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  cp -a "$REPO/dist/." .
  touch .nojekyll )

echo "[4/5] 提交"
( cd "$WT"
  git add -A
  if git diff --cached --quiet; then echo "    无变化，跳过提交"; else
    git commit -q -m "deploy: 静态站构建产物 $(date '+%Y-%m-%d %H:%M')"
  fi )

echo "[5/5] 推送"
( cd "$WT" && git push origin gh-pages )
echo "完成：https://ai1s.github.io/room3d/ （GitHub Pages 构建约需 30-90 秒）"
