import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const here = path.dirname(fileURLToPath(import.meta.url))

// The FastAPI server this console talks to. Override with API_URL=http://127.0.0.1:8001 npm run dev
const apiTarget = process.env.API_URL ?? 'http://127.0.0.1:8000'
// The console's own port. strictPort (below) means it fails loudly if 5175 is taken instead of silently moving.
const devPort = Number(process.env.CONSOLE_PORT ?? 5175)

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1', // loopback only: the backend has no authentication
    port: devPort,
    strictPort: true,
    fs: { allow: [here, path.resolve(here, '../tests/fixtures')] }, // the bundled example files live outside the app folder
    proxy: {
      // /api/health -> <apiTarget>/health, /api/normalize/... -> <apiTarget>/normalize/...
      '/api': { target: apiTarget, changeOrigin: false, rewrite: (p) => p.replace(/^\/api/, '') },
    },
  },
  build: { chunkSizeWarningLimit: 4000 }, // Monaco is one lazy-loaded chunk (local dev tool)
  preview: { host: '127.0.0.1' },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
})
