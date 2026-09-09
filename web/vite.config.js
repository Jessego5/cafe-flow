import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

// Three views, one build. The API paths are unprefixed, so the dev server
// proxies them individually to uvicorn; in production one container serves both
// and there is no proxy at all.
const API_PATHS = ['/menu', '/orders', '/queue', '/display', '/slots', '/stream', '/config']

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      API_PATHS.map((path) => [path, { target: 'http://127.0.0.1:8000', changeOrigin: true }]),
    ),
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: {
        student: resolve(__dirname, 'student.html'),
        barista: resolve(__dirname, 'barista.html'),
        display: resolve(__dirname, 'display.html'),
        // the component gallery: not a product surface, so `app/main.py` does
        // not route to it. Open frame.html directly
        frame: resolve(__dirname, 'frame.html'),
      },
    },
  },
})
