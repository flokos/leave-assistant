/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server proxies /api to the backend (published on :8000 by deploy/compose.dev.yaml); in Docker, nginx does it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000', changeOrigin: true } },
  },
  test: {
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    environment: 'jsdom',
    testTimeout: 20_000, // Ant Design tables/modals under jsdom take 4–6 s on the 4-core dev host
    setupFiles: ['./src/test-setup.ts'],
    globals: false,
  },
})
