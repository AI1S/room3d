import { defineConfig } from 'vite';

// 前端开发/预览时，只把 /api 转发给后端(8000)。
// 演示用的 /scans/demo/* 是 public/ 里的静态文件，不能走代理，
// 否则后端没起的时候会 502（这就是 W1 第一版踩的坑）。
export default defineConfig({
  base: './',
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
  preview: {
    port: 4173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
  build: { chunkSizeWarningLimit: 4000 },
});
