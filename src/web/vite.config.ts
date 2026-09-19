import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API lives in the Python server on 8000. In development Vite proxies to it
// so the frontend calls same-origin paths and nothing needs a base URL; in
// production the Python server serves this build from dist/ and the same paths
// resolve directly. One code path either way.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/shot': 'http://127.0.0.1:8000',
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
