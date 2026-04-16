import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const DEV_PORT = Number(process.env.VITE_DEV_PORT || 5173)
const API_TARGET = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: DEV_PORT,
    proxy: {
      '/api/ws': {
        target: API_TARGET.replace('http', 'ws'),
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
