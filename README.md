# Room3D · 3D 房源漫游（W1 骨架 + W2 一键管线）

手机拍一段视频 → 重建成高斯泼溅场景 → 网页里转着看房 → 用问答读户型。

**线上预览：https://ai1s.github.io/room3d/** （GitHub Pages，静态托管，已验证可渲染 / 可切房源；部署细节见 [docs/deploy.md](docs/deploy.md) 的 2C 节）

- **W1（已完成）**：先证明「一个链接打开就能 3D 看房 + 问问题」这条路通，场景用脚本合成的房间代替。
- **W2（本次）**：把「视频 / 重建产物 → 网页房源」串成一条命令，并加好静态部署配置。

> 仓库自带两套房源：`scans/demo`（脚本合成的两居室，演示用）和 `scans/selftest`（W2 管线自测：把演示场景转成 3DGS PLY 再转回 .splat 的往返结果，28079 点）。所以一打开就能看到顶栏的「房源下拉」；自己跑一次 `pipeline.py` 就会多出第三套。

![桌面端](docs/screenshots/desktop.png)
![手机端](docs/screenshots/mobile.png)

## 现在能做什么

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 3D 高斯泼溅场景加载 | ✅ | Spark 渲染器，支持 .ply / .splat / .ksplat / .spz / .sog |
| 手机 + 桌面自适应 | ✅ | 手机上 3D 视口自动让出下半屏给信息面板 |
| 多房间切换 | ✅ | 一个房源内多个场景（主卧 / 次卧） |
| 房源库切换 | ✅ W2 | 顶栏下拉，读 `scans/index.json`，2 套以上才出现；`#/scan/<id>` 可直接分享链接 |
| 户型信息卡 | ✅ | 面积、朝向、居室、重建点数 |
| 户型问答（规则替身） | ✅ | 预置问题 + **拒答分支**（没采集到的直接说"无法查看"） |
| 本地文件直接拖进来 | ✅ | 拿 Postshot / Luma / SuperSplat 导出的文件免部署也能看 |
| 朝向修正 / 自动旋转 / 重置视角 | ✅ | 真实扫描常见"躺平/倒挂"，一键切 Y↑ / Z↑ |
| **3DGS PLY → .splat 转换** | ✅ W2 | `scripts/ply_to_splat.py`：SH0 颜色、log 尺度、四元数编码、透明度剪枝、降采样 |
| **视频 → 发布一条龙** | ✅ W2 | `py scripts/pipeline.py`：抽帧 → 收重建产物 → 转格式 → 发布 → 重建索引 |
| **静态部署配置** | ✅ W2 | `vercel.json` + `public/_headers`（Cloudflare Pages），.splat 走 octet-stream + 长缓存 |
| 上传视频 → 抽帧 API | ✅ | FastAPI（只在本地跑），需要本机装 ffmpeg |
| VLM 出结构化 JSON | ⬜ W4 | meta.json 现在是手写的 / 管线生成占位，W4 由 VLM 生成 |
| LLM 问答 | ⬜ W5 | 现在是关键词匹配的替身，顺序（先判"不能答"）已经定好 |

## 跑起来

```bash
# 1) 装依赖（node 18+）
npm install

# 2) 生成两个示例房间（纯 python 标准库，无第三方依赖）
npm run sample        # = py scripts/make_sample_splat.py

# 3) 开发模式（热更新，http://localhost:5173）
npm run dev

# 4) 或者构建后预览（http://localhost:4173）
npm run build && npm run preview
#    注意用 localhost 而不是 127.0.0.1：vite 默认只监听 IPv6 的 localhost
```

后端（可选，只有网页上传视频/抽帧才需要）：

```bash
py -m pip install -r requirements.txt
py -m uvicorn server.main:app --reload --port 8000
# 自检： curl http://127.0.0.1:8000/api/health
```

> Windows 上 `python` 可能不在 PATH，用 `py`。前端 `npm run dev` 只把 `/api` 代理到 8000。

## W2：一条命令把视频变成网页房源

```bash
# A. 从视频开始（会自动抽帧，然后停在"该重建了"这一步）
py scripts/pipeline.py --video D:/dorm.mp4 --id dorm --title 宿舍

# B. 重建产物已经有了（Postshot 导出的 ply / 现成的 splat）
py scripts/pipeline.py --id dorm --title 宿舍 --splat "C:/Users/me/Downloads/dorm.ply"

# C. 抽帧抽过了，只补重建产物
py scripts/pipeline.py --id dorm --title 宿舍 --skip-frames --splat C:/x/dorm.ply

# D. 手工改了 meta.json / 加了新目录之后，只重建索引
py scripts/pipeline.py --reindex     # = npm run index
```

管线做了什么：

1. **抽帧**：`ffmpeg -vf fps=2,scale=min(1600,iw):-2` → `data/scans/<id>/frames/`（没装 ffmpeg 会直接告诉你装法：`winget install Gyan.FFmpeg`）
2. **收重建产物**：`--splat` 指定 / 把 PLY 丢进 `data/scans/<id>/rebuild/` / `--rebuild-cmd` 跑自己的命令（`{frames}` `{out}` 占位符）
3. **转格式**：`ply_to_splat.py` 把 3DGS PLY 转成 32 B/点的 .splat（默认剪掉透明度 < 0.005 的点）
4. **发布**：`public/scans/<id>/scene.splat` + `meta.json`（面积/朝向占位），最后重写 `public/scans/index.json`

重建这步脚本**不替你跑**（要 GPU）：推荐 **Postshot**（Windows 图形界面，直接导视频最省事）。
把导出 PLY 丢进 `data/scans/<id>/rebuild/` 再跑一次管线即可。

常用参数：

```bash
--max-points 800000     # 发布版压到 80 万点（手机低端机能跑）
--up z-to-y            # COLMAP/部分 3DGS 导出是 Z 朝上，转成 Y 朝上
--quat-order xyzw      # 画面出现"梳齿/条纹"时切这个（四元数分量顺序不对）
--keep-meta            # 只换 splat，保留已经填好的 meta.json
```

## 部署（静态站）

前端是纯静态的，`vite.config.js` 里 `base: './'`，丢到任何静态托管都能跑。
**完整步骤（GitHub 建仓 → push → Vercel/CF 部署 → 上线自检 4 条）见 [docs/deploy.md](docs/deploy.md)**，这里只放最短路径。

**Vercel**

```bash
vercel login    # 本机已装 CLI（66.6.0）
npm run deploy  # = vercel --prod，第一次会问项目名，选 room3d，其余按默认
# 只想要预览链接：npm run deploy:preview
```

> Windows 上 CLI 首次部署可能报 `EPERM ... symlink ... builders-vlt`（系统不让普通进程建符号链接）。
> 遇到就直接用**网页导入**：vercel.com → Add New → Project → 选 GitHub 仓库 → Deploy，构建在云端跑。
> 想坚持用 CLI 就开「开发者模式」或用管理员终端。

`vercel.json` 已配好：构建命令、输出目录，以及 `/scans/*.splat` 返回 `application/octet-stream` + 一年长缓存（不配的话有些浏览器会把 .splat 当文本处理）。

**Cloudflare Pages**

构建命令 `npm run build`，输出目录 `dist`，仓库里 `public/_headers` 会被拷进 `dist/_headers`，缓存头自动生效。

**Netlify / GitHub Pages**：构建命令同上，发布目录 `dist` 即可。

> ⚠️ **后端 FastAPI 不部署**：抽帧、上传这些只在本地跑。线上只有 `dist/` 里的静态文件（网页 + .splat + meta.json）。
> 所以别人打开链接只能看，不能上传；想给人上传再单独找带 GPU 的后端，W2 不做。
> 另：`.splat` 单个文件建议 ≤ 15 MB，GitHub/Vercel 单文件上限是 100 MB，别把 200 MB 的高清版塞进仓库。

## 目录

```
room3d/
├─ index.html                 # 单页骨架：顶栏 / 房源下拉 / 场景切换 / 信息面板 / 问答 / 工具
├─ src/
│  ├─ main.js                 # 渲染 + 载入 + 相机适配 + 房源库 + 问答（W1 用规则替身）
│  └─ style.css               # 深色 + 金色点缀，移动端改底部抽屉
├─ public/
│  ├─ _headers                # Cloudflare Pages 缓存头
│  └─ scans/
│     ├─ index.json           # 房源列表（pipeline.py 生成，前端顶栏下拉读它）
│     ├─ demo/                # 合成示例：meta.json + scene.splat(6.9MB) + scene2.splat(5.0MB)
│     └─ <id>/                # 真实房源：scene.splat + meta.json
├─ scripts/
│  ├─ pipeline.py             # W2 一键管线：视频/PLY → public/scans/<id>/
│  ├─ ply_to_splat.py         # 3DGS PLY → .splat 转换（numpy 可选，纯 stdlib 也能跑）
│  ├─ make_sample_splat.py    # 生成示例 .splat（合成房间，无第三方依赖）
│  └─ extract_frames.py       # 单独抽帧（调 ffmpeg）
├─ server/main.py             # FastAPI：/api/health、上传视频抽帧、上传 splat、列举
├─ docs/
│  ├─ deploy.md               # 部署手册：GitHub → Vercel / Cloudflare Pages
│  ├─ capture-sop.md          # 拍摄 SOP（拍之前看这份）
│  ├─ roadmap.md              # 8 周计划 + 验收标准
│  └─ screenshots/            # 验收截图
├─ vercel.json                # 部署配置（构建命令 + .splat 的 content-type / 缓存头）
└─ requirements.txt
```

## 接一个真实场景（完整 checklist）

1. 按 `docs/capture-sop.md` 拍 2–3 分钟视频（锁 AE/AF、1x、走慢三步、回起点闭合）；
2. `py scripts/pipeline.py --video D:/scan.mp4 --id mine --title 我家` → 自动抽帧，停在"该重建了"；
3. Postshot 导入视频或 `data/scans/mine/frames/`，训练完导出 PLY；
4. 把 PLY 丢进 `data/scans/mine/rebuild/`，再跑 `py scripts/pipeline.py --id mine --title 我家 --skip-frames`；
5. `npm run dev` 打开就能看到，顶栏下拉切房源；方向不对点「修正朝向」或在管线里加 `--up z-to-y`；
6. 打开 `public/scans/mine/meta.json` 填面积/朝向/居室/FAQ，把关键尺寸用卷尺或 A4 纸核一遍。

## 已知坑（踩过并修好的，别再踩）

- **`/scans` 不要做代理**：演示数据在 `public/`，vite 里把 `/scans` 代理到后端会导致 502。现在只代理 `/api`。
- **ffmpeg 未安装**：管线会直接告诉你装法，不要硬跑。`winget install Gyan.FFmpeg`，装完重开终端。
- **PLY 转完"表面像梳子/条纹"**：四元数分量顺序不对，加 `--quat-order xyzw` 重转（3DGS 官方是 wxyz）。
- **真实场景"躺平"或"倒挂"**：COLMAP 常是 Z 朝上，加 `--up z-to-y`；临时看可以在网页点「修正朝向」。
- **手机上看不到场景中心**：3D 画布会占满整屏而面板压住下半屏 → 看到的是一片墙。已用 `layout()` 把画布高度让出来。
- **vite 只监听 IPv6**：用 `localhost:5173` 打开；`127.0.0.1` 可能连不上（手机预览要加 `--host`）。
- **dev server 开着的时候改 `src/` 里的文件**：Windows 上偶尔报 `EIO`/`ReplaceFileW`（文件被 vite 监听占用），先停 dev server 再改。
- **大文件**：场景是 7 MB 左右的静态文件，先看进度条（`onProgress`），不要以为卡死。
- **打包体积**：`three` + `spark` 打包后 JS 约 3.2 MB（gzip 1.08 MB）。W2 只做了 .splat 长缓存，代码拆分留到以后。

## 体积账（为什么不像原方案那样卡 100 MB）

| 格式 | 每点字节 | 200 万点 | 备注 |
| --- | --- | --- | --- |
| 3DGS PLY (sh0) | 56 B | 112 MB | 训练默认导出 |
| .splat | 32 B | 64 MB | 常见网页格式（本项目用这个） |
| SOG / 压缩 | ~2–6 B | 5–12 MB | 需要额外压缩步骤 |

结论：**别写"整包 ≤ 100 MB"**，写成"首屏按 LOD 流式加载，单房间首屏 ≤ 10 MB"。
示例场景 6.9 MB / 22 万点，属于"能看但不够细"，真实房源目标是 50–100 万点、首屏 8–15 MB（管线里用 `--max-points` 压）。

## 合规红线（写进答辩也不要改）

- **不承诺"面积准确"**：房产面积是产权证概念，平台只提供「户型尺寸参考 + 关键尺寸可核验」。
- **不脑补未采集区域**：镜头没进过的地方统一回"没有采集到，无法查看"。
- **后台保留人工复核**：所有尺寸标为"重建量测参考值"。
