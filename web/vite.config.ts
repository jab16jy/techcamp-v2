/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// The browser always calls the relative /api/v1 (docs/04-api.md's
// convention, `main.py` mounts every router there — #21 round 10).
// `VITE_API_URL` is this dev proxy's only job: it picks where that one
// prefix is forwarded (`infra/compose.yaml`: api on API_PORT, default 8000)
// and never changes what the browser itself requests.
const API_PROXY_TARGET = process.env.VITE_API_URL ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  server: {
    proxy: { '/api/v1': API_PROXY_TARGET },
  },
  plugins: [
    tailwindcss(),
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'TechCamp',
        short_name: 'TechCamp',
        description:
          'Riego y monitoreo de campo para pequeños productores del Caribe colombiano.',
        lang: 'es',
        start_url: '/',
        display: 'standalone',
        background_color: '#efe7d6',
        theme_color: '#1f4a32',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          {
            src: '/icon-512-maskable.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // Precaches the whole shell (JS/CSS/HTML/icons), so the app opens offline after
        // one online visit — the dev-only /dev/ui chunk never exists in this output to precache.
        globPatterns: ['**/*.{js,css,html,svg,png,ico}'],
      },
    }),
  ],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Node's native `fetch`/`Request` (unlike a real browser's) require an
    // absolute URL — a bare page origin never exists under Vitest — so
    // `openapi-fetch`'s internal `new Request(path)` throws for the app's
    // real relative `''` base. Tests only; the browser always resolves the
    // relative `/api/v1` fine (see `client.ts`).
    env: { VITE_API_URL: 'http://localhost' },
  },
})
