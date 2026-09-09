/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // MapLibre создаёт свой воркер как модульный: new Worker(url, {type:'module'}).
  // Сборка воркеров по умолчанию идёт в iife — формат для модульного воркера
  // неподходящий. См. комментарий в src/screens/map/MapView.tsx.
  worker: { format: 'es' },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    // Интеграционные тесты поднимают весь каркас поверх MSW и ждут повторных
    // попыток запросов; пяти секунд по умолчанию на это не хватает.
    testTimeout: 20_000,
  },
})
