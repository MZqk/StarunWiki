import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/qa': { target: 'http://127.0.0.1:8091', changeOrigin: false },
      '/healthz': { target: 'http://127.0.0.1:8091', changeOrigin: false },
    },
  },
})
