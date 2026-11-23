# 部署手册（GitHub → 静态托管）

> 目标：把仓库推上 GitHub，再用 Vercel / Cloudflare Pages 接上去，得到一个**能直接发人的链接**。
> 线上只有 `dist/` 里的静态文件（HTML + JS + .splat）。**FastAPI 后端不部署** —— 上传视频、抽帧、重建全都只在本地做，网页只读已经生成的 `scans/`。

## 0. 先确认能构建

```bash
npm run build          # 产物在 dist/
npm run preview        # http://localhost:4173 打开验证一遍（用 localhost，别用 127.0.0.1）
```

构建通过再往下走。**dist/ 不要提交**（已在 .gitignore 里），托管平台会自己构建。

## 1. GitHub 首次提交

本机已经初始化好仓库并做了第一次提交（分支 `main`）。剩下只有「建远程仓库 + push」：

1. 打开 https://github.com/new ，Repository name 填 `room3d`，**不要**勾 "Add a README / .gitignore / license"（仓库里已经有了），Private 或 Public 都行。
2. 建完 GitHub 会给你一段命令，用这两行（把 URL 换成你自己的）：

```bash
git remote add origin https://github.com/<你的用户名>/room3d.git
git push -u origin main
```

第一次 push 会弹浏览器登录（Git Credential Manager），登一次以后就不用再输密码。

> 仓库大小：约 13MB（.git 压完 7.2MB），主要是 `public/scans/demo/`（11.8MB）和 `public/scans/selftest/`（0.9MB）里的 .splat。GitHub 单文件上限 100MB，够用。以后真实房源建议单个 .splat ≤ 15MB（见 README「体积账」）。

## 2A. Vercel 部署（推荐，最省事）

仓库里的 `vercel.json` 已经配好：`npm run build` → `dist`，并给 `/scans/*.splat` 加了 `application/octet-stream` 和一年 immutable 缓存。

**方式一：网页导入（最稳，推荐先用这个）**

vercel.com → Add New → Project → Import Git Repository → 选 `room3d` → Framework 自动识别 Vite，直接 Deploy。
构建在 Vercel 的服务器上跑，本机什么都不用装；之后每次 `git push` 自动重新部署，PR 还给 preview 链接。

> 这也是本机唯一确认可行的方式：本机 Windows 上 Vercel CLI 的远程 builder 安装会报
> `EPERM: operation not permitted, symlink ... com.vercel.cli\Cache\builders-vlt`（Windows 默认不给非管理员建符号链接）。
> 详见文末「常见问题」。

**方式二：CLI（本机已经装了 vercel 66.6.0）**

```bash
vercel login           # 选 GitHub 或邮箱登录，会开浏览器
npm run deploy         # = vercel --prod
```

第一次 `vercel` 会问几个问题，照这样答：

| 问题 | 答 |
| --- | --- |
| Set up and deploy? | Y |
| Which scope? | 你自己的账号 |
| Link to existing project? | N |
| Project name? | room3d |
| In which directory is your code located? | `./` |
| Want to modify these settings? | N（vercel.json 已经写好了） |

跑完会打印一个 `https://room3d-xxxx.vercel.app` 的地址，那就是分享链接。

**方式二：网页导入（以后改代码自动发布）**

vercel.com → Add New → Project → Import Git Repository → 选 `room3d` → Framework 自动识别 Vite，直接 Deploy。
之后每次 `git push` 都会自动重新部署，PR 还会给你 preview 链接。

## 2B. Cloudflare Pages（备选，国内访问有时更稳）

`public/_headers` 已经写好同样的缓存/MIME 规则，构建时会进 `dist/_headers`。

网页方式：dash.cloudflare.com → Workers & Pages → Create → Pages → Connect to Git → 选仓库，构建配置填：

- Framework preset: `Vite`
- Build command: `npm run build`
- Build output directory: `dist`

命令行方式（不接 Git）：

```bash
npm run build
npx wrangler pages deploy dist --project-name room3d
```

## 3. 部署完要自己验这 4 条

```bash
# 把 BASE 换成你的线上地址
BASE=https://room3d-xxxx.vercel.app

curl -sI $BASE/scans/demo/scene.splat | head -5     # 期望 200 + content-type: application/octet-stream
curl -s  $BASE/scans/index.json | head -5           # 期望 JSON，能看到 demo / selftest
```

浏览器里再点一遍：

- [ ] 打开首页能在几秒内出现房间（不是一直"正在加载"）
- [ ] 顶栏「房源下拉」能切到另一套
- [ ] 直接打开 `$BASE/#/scan/selftest` 能进对房源
- [ ] 问「衣柜内部能看到吗」→ 回"这部分没有采集到，无法查看"

## 4. 常见问题

| 症状 | 原因 / 修法 |
| --- | --- |
| 页面白屏，控制台 `Failed to fetch ... 502` | 本地 dev server 的 `/api` 代理指向了没启动的 8000；确认只代理 `/api`，`/scans` 走静态文件 |
| 场景 404 | `dist/scans/<id>/scene.splat` 没生成 → 本地跑 `npm run index` 重建索引后重新 build |
| 下载 .splat 很慢 / 浏览器二次请求 | 缓存头没生效：Vercel 看 `vercel.json` 的 `headers`，Cloudflare 看 `dist/_headers` 是否随构建产出 |
| 手机上打开一片黑 | 手机上可见高斯点预算只有 50–80 万；用 `ply_to_splat.py --max-points 600000` 单独出一份移动版 |
| 换房源后标题不对 | 已经修过：`loadSplat` 直接用 label 设标题，不再靠相对 url 匹配 |
| `vercel deploy` 报 `EPERM ... symlink ... builders-vlt`（本机实测） | Windows 不给普通进程创建符号链接，Vercel CLI 装远程 builder 时卡在这。三条路：①**直接用网页导入**（推荐，构建在云端跑，不碰本地符号链接）；②开「开发者模式」（设置 → 系统 → 开发者选项 → 开发人员模式，或 `start ms-settings:developers`）后重开终端再 `npm run deploy`；③用管理员权限开一个终端跑。 |
| `vercel` 提示 Logged out | 先 `vercel login`（会开浏览器）；不登录只能用 `--temporary`，本机因上面的符号链接问题走不通 |

## 5. 回滚

Vercel / Cloudflare 的部署页面上每次部署都有 immutable 的 URL，点上一次的即可回滚；也可以 `git revert` 后重新 push。
```
