import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const proxy = {
  '/api': {
    target: 'http://127.0.0.1:8001',
    changeOrigin: true,
  },
}

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5174,
    strictPort: true,
    allowedHosts: true,
    proxy,
  },
  // cpolar 面试演示建议用 npm run build + npm run preview，
  // 静态构建产物比 Vite dev 按模块加载快很多。
  preview: {
    host: '0.0.0.0',
    port: 5174,
    strictPort: true,
    allowedHosts: true,
    proxy,
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500,
  },
})
