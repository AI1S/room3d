import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';

/* ------------------------------------------------------------------ *
 * Room3D · W1 骨架
 * 目标：一个链接，手机/电脑打开就能 3D 漫游 + 看户型信息 + 问 3 个问题。
 * 数据来源有两层：
 *   1) public/scans/<id>/meta.json + scene.splat   （静态演示，默认）
 *   2) 后端 /api/scans 返回的实时任务              （W2 再打开）
 * ------------------------------------------------------------------ */

const $ = (id) => document.getElementById(id);
const BASE = new URL('.', document.baseURI);

const state = {
  meta: null,
  mesh: null,
  upAxis: 'y', // 'y' 正常 | 'z' Z 轴朝上（很多 COLMAP/3DGS 导出是这种）
  loading: false,
  scans: [],   // W2：public/scans/index.json 里的房源列表
  scanId: null,
  view: null,  // meta.camera：真实扫描的默认机位（来自 COLMAP 位姿），没有就用启发式取景
};

/* ---------------- 渲染器 ---------------- */
const app = $('app');

if (!window.WebGL2RenderingContext) {
  $('overlayText').textContent = '这台设备/浏览器不支持 WebGL2，换 Chrome / Edge / Safari 新版本试试。';
  throw new Error('WebGL2 not supported');
}

const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x0a0b0d, 1);
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const BASE_FOV = 70;
const camera = new THREE.PerspectiveCamera(BASE_FOV, window.innerWidth / window.innerHeight, 0.05, 600);
camera.position.set(0, 1.6, 3);

const spark = new SparkRenderer({ renderer });
scene.add(spark);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.rotateSpeed = 0.55;
controls.zoomSpeed = 0.8;
controls.panSpeed = 0.6;

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

/** 手机上信息面板占了下半屏，3D 视口就只渲染上半屏——不然画面中心会被面板挡住，
 *  看到的就是一片墙（W1 第一版手机截图就是这个毛病）。 */
function layout() {
  const isMobile = window.matchMedia('(max-width: 760px)').matches;
  const panel = $('panel');
  const panelShown = panel && !panel.classList.contains('hidden');
  let h = window.innerHeight;
  if (isMobile && panelShown) {
    h = Math.max(260, window.innerHeight - Math.round(panel.getBoundingClientRect().height));
  }
  app.style.cssText = 'position:fixed;top:0;left:0;right:0;height:' + h + 'px;';
  renderer.setSize(window.innerWidth, h);
  camera.aspect = window.innerWidth / h;
  camera.updateProjectionMatrix();
}

window.addEventListener('resize', () => {
  layout();
});

/* ---------------- 工具函数 ---------------- */
const fmtMB = (bytes) => (bytes ? (bytes / 1024 / 1024).toFixed(1) + ' MB' : '');

function showOverlay(text, sub = '') {
  $('overlayText').textContent = text;
  $('overlaySub').textContent = sub;
  $('barFill').style.width = '0%';
  $('overlay').classList.remove('gone');
}
function hideOverlay() {
  $('overlay').classList.add('gone');
}
function setProgress(loaded, total) {
  const sub = fmtMB(loaded);
  const pct = total > 0 ? Math.min(100, (loaded / total) * 100) : 0;
  $('barFill').style.width = (pct || 8) + '%';
  $('overlaySub').textContent = sub ? '已传输 ' + sub : '';
}

/** 让相机落在场景内部偏中央的位置：室内漫游的默认视角 */
function fitView(mesh) {
  let box;
  try {
    box = mesh.getBoundingBox(true);
  } catch (e) {
    box = new THREE.Box3().setFromObject(mesh);
  }
  if (!box || box.isEmpty()) return;

  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 1;

  if (state.view && state.view.position && state.upAxis === 'y') {
    // 真实扫描：用 COLMAP 位姿里算出来的机位，直接站在拍摄者站过的地方
    const v = state.view;
    const t = v.target || [center.x, center.y, center.z];
    camera.position.set(v.position[0], v.position[1], v.position[2]);
    controls.target.set(t[0], t[1], t[2]);
    camera.fov = v.fov ? Math.min(90, Math.max(35, v.fov)) : BASE_FOV;
  } else {
    // 默认视角：站在房间里偏高的一侧往里看（室内看房视角），而不是站在墙外面
    camera.fov = BASE_FOV;
    controls.target.copy(center);
    const portrait = camera.aspect < 0.9 ? 1.18 : 1;
    camera.position.copy(center).add(
      new THREE.Vector3(size.x * 0.21 * portrait, size.y * 0.17, size.z * 0.30 * portrait)
    );
  }
  camera.near = Math.max(0.02, maxDim / 400);
  camera.far = Math.max(60, maxDim * 40);
  camera.updateProjectionMatrix();

  controls.minDistance = Math.max(0.15, maxDim * 0.05);
  controls.maxDistance = maxDim * 2.2;
  controls.update();
}

function applyUpAxis() {
  if (!state.mesh) return;
  state.mesh.rotation.set(state.upAxis === 'z' ? -Math.PI / 2 : 0, 0, 0);
  fitView(state.mesh);
}

/* ---------------- 载入场景 ---------------- */
function removeCurrentMesh() {
  if (!state.mesh) return;
  scene.remove(state.mesh);
  if (typeof state.mesh.dispose === 'function') {
    try { state.mesh.dispose(); } catch (e) { /* ignore */ }
  }
  state.mesh = null;
}

/** opts: { url } 或 { fileBytes, fileType, fileName } */
async function loadSplat(opts, label) {
  if (state.loading) return;
  state.loading = true;
  showOverlay('正在加载 ' + (label || '场景') + '…');
  removeCurrentMesh();

  try {
    const mesh = new SplatMesh({
      ...opts,
      onProgress: (ev) => setProgress(ev?.loaded || 0, ev?.total || 0),
    });
    scene.add(mesh);
    await mesh.initialized;
    state.mesh = mesh;
    applyUpAxis();
    hideOverlay();
    $('sceneTitle').textContent = label || (opts.fileBytes ? '本地文件' : '');
  } catch (err) {
    console.error('[room3d] load failed', err);
    showOverlay('场景加载失败', String(err && err.message ? err.message : err).slice(0, 120));
    $('overlaySub').textContent = '把 .ply / .splat 文件直接拖进页面也能看';
    setTimeout(() => hideOverlay(), 5000);
  } finally {
    state.loading = false;
  }
}

/* ---------------- 元数据与 UI ---------------- */
const FALLBACK_META = {
  title: '演示房源',
  captureNote: '示例数据：没有读到 meta.json，先用内置信息占位。',
  metrics: [
    { label: '面积', value: '—', big: true },
    { label: '朝向', value: '—' },
    { label: '房间', value: '—' },
    { label: '层数', value: '—' },
  ],
  scenes: [{ id: 'demo', name: '主卧', title: '演示房源 · 主卧', url: 'scans/demo/scene.splat' }],
  faq: [],
  unknown: [],
};

function renderMetrics(metrics) {
  const dl = $('metrics');
  dl.innerHTML = '';
  for (const m of metrics || []) {
    const wrap = document.createElement('div');
    const dt = document.createElement('dt');
    dt.textContent = m.label;
    const dd = document.createElement('dd');
    dd.textContent = m.value;
    if (m.big) dd.classList.add('big');
    wrap.append(dt, dd);
    dl.append(wrap);
  }
}

function renderChips(scenes) {
  const nav = $('chips');
  nav.innerHTML = '';
  if (!scenes || scenes.length <= 1) {
    nav.style.display = 'none';
    return;
  }
  nav.style.display = 'flex';
  scenes.forEach((s, i) => {
    const b = document.createElement('button');
    b.className = 'chip' + (i === 0 ? ' active' : '');
    b.textContent = s.name || s.id;
    b.onclick = () => {
      document.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
      b.classList.add('active');
      loadSplat({ url: resolveUrl(s.url) }, s.title || s.name);
    };
    nav.append(b);
  });
}

function resolveUrl(u) {
  if (/^(https?:)?\/\//.test(u)) return u;
  return new URL(u.replace(/^\/+/, ''), BASE).href;
}

/* ---------------- 问答（W1 用规则替身，W5 再接 LLM） ---------------- */
function botSay(text, cls = 'bot') {
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  $('chat').append(div);
  $('chat').scrollTop = $('chat').scrollHeight;
}

/** W1 的顺序：先判"不能答"，再看"能答"。W5 换成 LLM 后这个顺序不能变。 */
function answer(question) {
  const q = question.trim();
  if (!q) return;
  const meta = state.meta || FALLBACK_META;

  // 1) 没采集的区域：直接说没有
  for (const area of meta.unknown || []) {
    if (q.includes(area)) {
      botSay('「' + area + '」这次没有采集到，无法查看。\n（不靠大模型猜：镜头没进过的地方，我们不给结论。）', 'warn');
      return;
    }
  }
  // 2) 采到了外壳但相机没进过的地方：内部、柜子里、床底、窗外
  for (const rule of meta.refuse || []) {
    const hitAll = (rule.keywords || []).some((k) => q.includes(k));
    if (hitAll) { botSay(rule.answer, 'warn'); return; }
  }
  // 3) 有据可查的事实问题
  for (const item of meta.faq || []) {
    const hit = (item.keywords || []).some((k) => q.includes(k));
    if (hit && item.answer) { botSay(item.answer); return; }
  }
  botSay(
    '演示版还没接大模型，只能回答预置的几个问题。\n正式版接上 LLM 后规则不变：只回答 JSON 里有的事实；没有采集的地方直接说「无法查看」，不脑补。',
    'warn'
  );
}

/** 只重挂"建议问题"按钮；提交表单的监听器全页只挂一次（不然切房源会重复触发）。 */
function wireSuggest(meta) {
  const suggest = $('suggest');
  suggest.innerHTML = '';
  (meta.faq || []).slice(0, 3).forEach((item) => {
    const b = document.createElement('button');
    b.textContent = item.question || (item.keywords || [])[0] || '问题';
    b.onclick = () => { botSay(b.textContent, 'user'); answer(b.textContent); };
    suggest.append(b);
  });
}

const CHAT_INTRO = '试试问我：这套房多大？朝什么方向？衣柜内部能看到吗？';

function resetChat() {
  $('chat').innerHTML = '';
  botSay(CHAT_INTRO);
}

$('askForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = $('askInput');
  const q = input.value;
  if (!q.trim()) return;
  botSay(q, 'user');
  answer(q);
  input.value = '';
});

/* ---------------- 房源库（W2：pipeline.py 生成的 scans/index.json） ---------------- */
const DEMO_SCAN = { id: 'demo', title: '演示房源', metaUrl: 'scans/demo/meta.json' };

async function fetchJson(url) {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(url + ' -> HTTP ' + res.status);
  return res.json();
}

async function loadScanLibrary() {
  try {
    const idx = await fetchJson(new URL('scans/index.json', BASE).href);
    const scans = (idx.scans || []).filter((s) => s && s.id && s.metaUrl);
    if (scans.length) return scans;
  } catch (e) {
    console.warn('[room3d] scans/index.json 读不到，退回内置 demo：', e && e.message);
  }
  return [DEMO_SCAN];
}

// 窄屏把「演示房源 · 两居室（合成场景）」压成「演示房源」，避免顶栏被撑爆
function scanLabel(title) {
  const full = title || '';
  if (window.innerWidth > 760) return full;
  return full.split(/[·（(]/)[0].trim() || full;
}

function renderScanSelect(scans, currentId) {
  const sel = $('scanSelect');
  sel.innerHTML = '';
  for (const s of scans) {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = scanLabel(s.title || s.id);
    opt.title = s.title || s.id;
    sel.append(opt);
  }
  sel.value = currentId;
  sel.hidden = scans.length < 2;
  sel.onchange = () => {
    const next = state.scans.find((s) => s.id === sel.value);
    if (next) loadScan(next);
  };
}

function scanFromHash() {
  const m = /#\/scan\/([\w-]+)/.exec(location.hash || '');
  if (!m) return null;
  return state.scans.find((s) => s.id === m[1]) || null;
}

function applyMeta(meta, entry) {
  state.meta = meta;
  state.scanId = entry.id;
  state.view = meta.camera || null;
  document.title = (meta.title || entry.title || 'Room3D') + ' · Room3D';
  $('sceneTitle').textContent = meta.title || entry.title || '';
  renderMetrics(meta.metrics || []);
  $('captureNote').textContent = meta.captureNote || '';
  renderChips(meta.scenes || []);
  wireSuggest(meta);
  resetChat();
  renderScanSelect(state.scans, entry.id);
  try { history.replaceState(null, '', '#/scan/' + entry.id); } catch (e) { /* file:// 下会抛，忽略 */ }
  layout();
}

async function loadScan(entry) {
  showOverlay('正在读取「' + (entry.title || entry.id) + '」…');
  let meta;
  try {
    meta = await fetchJson(resolveUrl(entry.metaUrl));
  } catch (e) {
    console.warn('[room3d] 读不到 ' + entry.metaUrl, e);
    meta = FALLBACK_META;
  }
  applyMeta(meta, entry);

  const first = (meta.scenes || [])[0];
  if (!first) {
    showOverlay('这套房源还没有场景', '先跑 py scripts/pipeline.py 发布一个 .splat');
    return;
  }
  await loadSplat({ url: resolveUrl(first.url) }, first.title || first.name || entry.title);
}

/* ---------------- 启动 ---------------- */
async function boot() {
  showOverlay('正在读取房源列表…');
  state.scans = await loadScanLibrary();
  await loadScan(scanFromHash() || state.scans[0]);
}

window.addEventListener('hashchange', () => {
  const hit = scanFromHash();
  if (hit && hit.id !== state.scanId) loadScan(hit);
});

// 横竖屏切换 / 改窗口宽度时，房源下拉的标签要重新裁短
window.addEventListener('resize', () => {
  if (state.scans && state.scans.length) renderScanSelect(state.scans, state.scanId);
});

/* ---------------- 交互 ---------------- */
$('btnTogglePanel').onclick = () => {
  $('panel').classList.toggle('hidden');
  requestAnimationFrame(layout);
};

$('btnReset').onclick = () => {
  if (state.mesh) { applyUpAxis(); }
};

$('btnFlip').onclick = () => {
  state.upAxis = state.upAxis === 'y' ? 'z' : 'y';
  applyUpAxis();
  botSay('已切换坐标朝向（' + (state.upAxis === 'y' ? 'Y 轴朝上' : 'Z 轴朝上') + '）。真实扫描如果上下颠倒或者躺平，点这里。', 'warn');
};

$('btnAuto').onclick = () => {
  controls.autoRotate = !controls.autoRotate;
  controls.autoRotateSpeed = 0.6;
  $('btnAuto').style.color = controls.autoRotate ? 'var(--accent)' : '';
};

const fileInput = $('fileInput');
$('btnDrop').onclick = () => fileInput.click();
fileInput.onchange = () => {
  const f = fileInput.files && fileInput.files[0];
  if (f) loadLocalFile(f);
  fileInput.value = '';
};

async function loadLocalFile(file) {
  const ext = (file.name.split('.').pop() || '').toLowerCase();
  if (!['ply', 'splat', 'ksplat', 'spz', 'sog'].includes(ext)) {
    botSay('不认识这个格式：.' + ext + '。支持 .ply / .splat / .ksplat / .spz / .sog', 'warn');
    return;
  }
  const buf = await file.arrayBuffer();
  await loadSplat({ fileBytes: buf, fileType: ext, fileName: file.name }, file.name);
}

// 拖拽
let dragDepth = 0;
window.addEventListener('dragenter', (e) => {
  e.preventDefault();
  dragDepth++;
  document.body.classList.add('dragging');
});
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('dragleave', (e) => {
  e.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) document.body.classList.remove('dragging');
});
window.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove('dragging');
  const f = e.dataTransfer?.files?.[0];
  if (f) loadLocalFile(f);
});

// 快捷键
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'r' || e.key === 'R') $('btnReset').click();
  if (e.key === 'h' || e.key === 'H') $('btnTogglePanel').click();
});

boot();

// 调试钩子（只在本机用 ?debug=1 时暴露，便于诊断相机与包围盒）
if (new URLSearchParams(location.search).has("debug")) {
  window.__r3d = { THREE, camera, controls, scene, spark, renderer, state, fitView };
}
