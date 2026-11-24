# 踩坑记录（真实时间线）

> 为什么有这个文件：README 里的「已知坑」是结论，这里是过程——**真机上真出过的错、原始报错、当时的误判、最后怎么修的**。
> 记录格式：现象 → 原因 → 修法 → 教训。只写踩过的，不写「应该注意」的。
> 配套阅读：[docs/capture-sop.md](capture-sop.md) 六点五节（一条真实视频的失败反例与数字出处）。

## 约定

- 日期只在确定时写（Git 提交时间、终端日志时间），否则写「W1 搭建期间」这样的阶段。
- 报错字符串原样抄，不做美化——以后 grep 靠它。
- 每条都留「教训」，因为下一个坑往往是同一个教训的另一种形态。

## 阶段 0 · 环境摸底（W1 之前，未记日期）

### 0.1 ffmpeg「没装」——其实装了，只是不在 PATH

- **现象**：`where ffmpeg` 空；管线第一步直接报缺 ffmpeg，我以为是环境不干净。
- **原因**：早先用 winget 装过 Gyan.FFmpeg，再装只回 `existing package already installed`；
  可执行文件躺在 `${LOCALAPPDATA}/Microsoft/WinGet/Packages/Gyan.FFmpeg_*/ffmpeg-9.1-full_build/bin`，没进 PATH。
- **修法**：脚本里按「PATH → 硬编码目录」两级查找；查不到才提示 `winget install Gyan.FFmpeg`。
- **教训**：报「缺工具」之前先 `winget list`，Windows 上的包管理器装完不一定给你 PATH。

### 0.2 Python 只有 `py`，没有 `python`

- 本机 `python` 不在 PATH，`py` = 3.14.6，numpy 2.4.4 / Pillow 12.2.0 可用。
- 所有脚本统一用 `py scripts/xxx.py` 调用，README 里也这么写。

## 阶段 1 · W1 网页骨架

### 1.1 页面卡在「正在加载」，控制台 `Failed to fetch ... 502 Bad Gateway`

- **原因**：`vite.config.js` 里配了 `server.proxy`，而 `preview` **会继承** dev 的 proxy，
  把 `/scans/*` 转发到没启动的后端 8000 端口。
- **修法**：只代理 `/api`；后端产物改走 `/api/scans/<id>/<file>`，静态文件留在 `public/`。
- **教训**：preview 不是「纯静态服务器」，它复用了 dev 配置；部署前必须用 preview 而不是 dev 验一遍。

### 1.2 切换场景后顶栏标题不更新

- **原因**：`loadSplat()` 里用已经 `resolveUrl()` 成绝对地址的 `opts.url`，去
  `meta.scenes.find(s => s.url === opts.url)` 里比对相对路径，永远匹配不上。
- **修法**：不再反查，直接用调用方传进来的 `label` 赋给 `#sceneTitle`。
- **教训**：凡是「相对路径 vs 绝对路径」的等值比较，先想清楚两边是不是同一个世界。

### 1.3 手机竖屏只看得到一片墙

- **现象**：3D 画布铺满整屏，信息面板盖住下半屏，手机上一眼是近处墙面，像没加载。
- **修法**：`layout()` —— ≤760 px 且面板可见时，把 app 高度设为 `innerHeight - 面板高度`（下限 260），
  再 `renderer.setSize` + 更新 `camera.aspect`；`boot()` / `resize` / 面板开关都调一次。
- **教训**：3D 页面在移动端的默认行为永远是错的，必须自己算可视区高度。

### 1.4 Edge 的 `--headless --screenshot` 截不到渲染结果

- **现象**：加 `--virtual-time-budget=10000` 后场景只加载到 0.5 MB 就停，截图永远是加载遮罩。
- **原因**：virtual time 把网络请求也冻住了，大文件永远下不完。
- **修法**：要验证渲染就用 `playwright-core` + `channel: 'msedge'` 实时等待；纯静态页才用 Edge 直接截图。
- **教训**：自动化截图出问题，九成是「时间被加速了」而不是「页面坏了」。

## 阶段 2 · W2 管线（PLY → .splat）

### 2.1 往返测试读出 NaN：把 32 字节记录当成 56 字节

- **原因**：`.splat` 每点 = 3 float 位置 + 3 float 线性尺度 + 4 字节 RGBA + 4 字节四元数 = **32 B**；
  我在 struct 格式里写成了 `<12f4B4B`（56 B），逐点错位后 scale 变成天文数字、四元数变 NaN。
- **修法**：改为 `<6f4B4B`，并在脚本开头断言 `struct.calcsize(fmt) == 32`。
- **教训**：二进制格式先断言长度再读数据，错位比崩掉更难查。

### 2.2 Windows 控制台中文乱码（GBK）

- **现象**：脚本在 MSYS bash 里跑，中文输出成乱码。
- **修法**：脚本开头 `sys.stdout.reconfigure(encoding='utf-8')`。
- **教训**：跨终端的中文输出，编码要自己钉死，别指望环境。

### 2.3 dev server 开着的时候改 `src/` 报 `EIO`

- **现象**：编辑文件时报 `ReplaceFileW ... EIO (Win32 32)`。
- **原因**：文件被 vite 的文件监听占用，Windows 不允许替换。
- **修法**：先停 dev server 再改；新建文件不受影响。
- **教训**：Windows 上「热更新 + 改源码」天然冲突，脚本化批量改写前先停服务。

## 阶段 3 · 本地重建（COLMAP + Brush，2026-11-23）

### 3.1 `--SiftExtraction.use_gpu` 报 unknown option

- **原因**：COLMAP 4.2.1 把参数挪了位置：`--SiftExtraction.use_gpu` → `--FeatureExtraction.use_gpu`，
  `--SiftMatching.use_gpu` → `--FeatureMatching.use_gpu`。网上大量教程还是旧名字。
- **教训**：命令行工具的教程差一代就会全错；先 `--help` 再说。

### 3.2 Brush 装完没有 `brush.exe`

- 压缩包里只有 `brush_app.exe`，GUI 和 CLI 是同一个二进制；不加 `--with-viewer` 才是 headless。
- **教训**：名字对不上先 `ls`，别急着怀疑下载坏了。

### 3.3 `rebuild.sh` 里日志把返回值污染了

- **原因**：`DSET=$(run_sfm $FPS)` —— 函数里的进度日志也进了 stdout，变量里混进一堆日志行。
- **修法**：`run_sfm` 只把数据集路径 `echo` 到 stdout，其余全部 `>&2`。
- **教训**：用命令替换接返回值时，函数内任何 `echo` 都是隐患。

### 3.4 COLMAP 输出 `sparse/0`、`sparse/1`…，Brush 只认 `sparse/0`

- **修法**：脚本挑注册帧最多的那个模型（看 `images.bin` 大小）复制成 `sparse/0`。
- **教训**：mapper 会生成多个候选模型，「最大的」不等于「第一个」。

### 3.5 抽帧 2 fps → 7 个碎片模型，每个只注册 2 张图

- **现象**：`Discarding reconstruction due to no initial pair`，7 个模型各 2 张图。
- **误判**：第一反应是算力/参数不够，去调 `peak_threshold`、`overlap`。
- **真相**：查 SQLite 的 `images` / `matches` 表 → 2387 对图像、**inlier 中位数 0**，只有 607 对 > 50。
  相邻帧间隔 0.5 s，转过的角度超过竖直手机的水平视场（约 50°），两帧根本拍的是不同方向。
- **修法**：fps 提到 10 重跑（1604 帧 → 194 张注册，12%），并在 `rebuild.sh` 里加「注册率 < 1/3 自动提帧率重试」。
- **教训**：建图失败先量**匹配质量**（inlier 分布），别急着调参数——参数治不了没重叠的帧。

### 3.6 把「帧间重叠不足」说成了「弱纹理位姿漂移」

- **现象**：给老师写邮件时，把 `inlier 中位数 0` 归因成「弱纹理白墙导致位姿漂移」。
- **真相**：中位数 0 意味着**相邻帧没匹配上**，连跟踪都没建立，谈不上漂移；
  白墙是另一件事（150–160 s 那段 sharp=0.7，本身是废帧）。
- **修法**：三种情况分开写，写进 capture-sop 六点五节；数字标出处。
- **教训**：**术语必须对得上观测**。汇报里一个用错的词，会被追问到崩。

### 3.7 清理得太早，第二天数字没有出处

- **现象**：把 `_diag/` 日志、诊断脚本、911 MB 数据集（`data/scans/dorm-test1b`）全删了；
  第二天写文档时，10 fps 那组的数字只剩记忆，无法给逐帧明细。
- **修法**：文档里如实标注「数据来源 + 日志已清理，仅结论可复述」；以后先导出摘要再删数据。
- **教训**：**先留证据（日志、关键帧、参数快照），再清理**。文档里每个数字都要能指回一条命令或一段日志。

## 阶段 4 · 部署（2026-11-23）

### 4.1 `git push` 报 `Failed to connect to github.com:443`

- **现象**：`curl` 报 `Operation timed out after 21055 ms`；本机直连 GitHub 不通。
- **修法**：Clash 在 `127.0.0.1:7890` 时，`git config --local http.proxy http://127.0.0.1:7890`（`https.proxy` 同值）。
- **教训**：网络问题先分层验证（直连 / 代理 / DNS），别一开始就怀疑凭据。

### 4.2 本机没有任何 git 凭据助手

- **现象**：system/global 的 `credential.helper` 全空，push 时无人应答。
- **修法**：装 GitHub CLI，`credential.helper '"C:/Program Files/GitHub CLI/gh.exe" auth git-credential'`；
  设备码登录在非交互终端也能跑（`gh auth login --web` 会把码写进剪贴板）。
- **教训**：Windows 上「绝对路径 + 引号」是 helper 能跑起来的前提。

### 4.3 加 GitHub Actions workflow 后 push 被拒

- **报错**：`refusing to allow an OAuth App to create or update workflow .github/workflows/pages.yml without workflow scope`。
- **修法**：改用 `gh-pages` 分支托管构建产物（不碰 workflow 文件），Pages 设为 legacy + 分支发布。
- **教训**：token 的 scope 决定你能改哪些文件；`gh auth refresh -s workflow` 也行，但当时不想再走设备码。

### 4.4 `vercel deploy` 报 `EPERM ... symlink ... @vercel/static-build`

- **原因**：Windows 普通进程不允许建符号链接，Vercel CLI 的构建器缓存目录要靠软链。
- **结论**：本机 CLI 部署不可行，改网页导入（构建在云端跑）；不改系统「开发者模式」。
- **教训**：跨平台工具的 Windows 短板，优先找它支持的替代路径，别为了一个 CLI 去改系统安全设置。

### 4.5 Vercel 注册 +86 收不到短信验证码

- **现象**：先是不来码，多次重试后提示 `The verification phone number has been temporarily blocked. Please wait 12 hours and try again.`
- **结论**：放弃 Vercel，改 GitHub Pages；意外收获是 Pages 对 `.splat` 的 MIME 天然是 `application/octet-stream`，
  反而不需要配 header（只是没有长缓存）。
- **教训**：上线路径要准备两条，别把交付卡在一个第三方注册页上。

## 一句话

这份文件存在的意义：文档可以抄，报错抄不了。
