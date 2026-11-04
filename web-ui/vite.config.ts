/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// 真实 API 由 scripts/serve-local.mjs 的回环网关转发。
export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    define: {
      __APP_VERSION__: JSON.stringify(process.env.npm_package_version ?? '0.1.0'),
    },
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
    },
    build: {
      sourcemap: false,
      chunkSizeWarningLimit: 600,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ['react', 'react-dom', 'react-router'],
            data: ['@tanstack/react-query', 'zustand'],
            markdown: ['react-markdown', 'remark-gfm', 'rehype-sanitize'],
            ui: [
              '@radix-ui/react-dialog',
              '@radix-ui/react-dropdown-menu',
              '@radix-ui/react-switch',
              '@radix-ui/react-tooltip',
              '@radix-ui/react-slot',
            ],
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./tests/setup.ts'],
      include: ['tests/**/*.test.{ts,tsx}'],
      css: false,
    },
  };
});
