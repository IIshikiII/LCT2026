/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Воркер MapLibre. Разбор обоих пунктов — в комментарии к wireMapWorker в
  // src/main.tsx.
  //
  // Исключение из предбандлинга нужно деву: иначе в `.vite/deps/` попадает
  // только `maplibre-gl.js`, а соседний файл воркера остаётся в пакете.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  // Формат нужен сборке: MapLibre запускает воркер модульным, а обычный скрипт
  // импортируется модулем без нареканий. ES-модуль в роли классического
  // воркера, наоборот, не стартует вовсе.
  worker: { format: 'iife' },
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
