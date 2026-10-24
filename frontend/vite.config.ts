import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

const nullModule = fileURLToPath(new URL('./src/shims/null-module.js', import.meta.url))

// The production app is served by the Python backend from frontend/dist (same origin as the API).
// `npm run dev` proxies API and websocket calls to a backend on :8000 (or GOBOT_API).
export default defineConfig({
  plugins: [preact()],
  resolve: {
    alias: { 'iconv-lite': nullModule, jschardet: nullModule },
  },
  server: {
    proxy: {
      '/api': { target: process.env.GOBOT_API ?? 'http://127.0.0.1:8000', ws: true },
    },
  },
  build: {
    target: ['safari15', 'chrome100'],
    sourcemap: false,
    chunkSizeWarningLimit: 800,
  },
})
