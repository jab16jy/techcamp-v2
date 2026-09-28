/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// The browser always calls the relative /api/v1 (docs/04-api.md's
// convention, `main.py` mounts every router there — #21 round 10).
// `API_PROXY_TARGET` is this dev proxy's only job: it picks where that one
// prefix is forwarded (`infra/compose.yaml`: api on API_PORT, default 8000).
// Deliberately not `VITE_`-prefixed: that prefix is for values Vite exposes
// to the browser bundle, and this one must never reach it (#21 round 11).
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

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
      // T9 needs its own `push` / `notificationclick` handlers, and Workbox's
      // generated worker cannot carry them, so the worker is hand-written
      // (`src/features/push/sw.ts`) and only the manifest is injected. The
      // precache globs below are E1's, moved verbatim from `workbox.globPatterns`
      // so the shell still opens offline exactly as before.
      //
      // `filename` is deliberately flat: the plugin resolves the emitted worker
      // as `resolve(root, outDir, filename)` but builds the bundle into `outDir`
      // root, so a nested `filename` builds `dist/sw.js` and then tries to
      // rename a `dist/features/push/sw.js` that was never written. `srcDir`
      // is what locates the source; `filename` is the emitted name.
      strategies: 'injectManifest',
      srcDir: 'src/features/push',
      filename: 'sw.ts',
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
      injectManifest: {
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
    // real relative `''` base. Tests only, and a variable of its own (not
    // `API_PROXY_TARGET`, which never reaches the browser bundle); the
    // browser always resolves the relative `/api/v1` fine (see `client.ts`).
    env: { VITE_API_TEST_BASE_URL: 'http://localhost' },
  },
})
