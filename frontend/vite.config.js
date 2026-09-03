import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
 export default defineConfig({
   plugins: [
     vue(),
   ],
   resolve: {
     alias: {
       '@': fileURLToPath(new URL('./src', import.meta.url)),
     },
   },
   server: {
     host: '0.0.0.0',          // 允许外部访问
     port: 5173,
     allowedHosts: [
       'creative-evasive-possibly.ngrok-free.dev',  // 你的 ngrok 域名
     ],
     proxy: {
       '/api': {
         target: 'http://127.0.0.1:8000',
         changeOrigin: true,
       },
     },
   },
 })
