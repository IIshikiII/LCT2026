/**
 * Точка доступа к тестовому серверу заглушек.
 *
 * Отдельный файл, а не прямой импорт из src/mocks/node, — чтобы в тестах было
 * видно, что сервер один на весь прогон и поднимается в setup.ts.
 *
 * Подменить ответ в одном тесте:
 *   server.use(http.get('*\/meta', () => new HttpResponse(null, { status: 500 })))
 */
export { server } from '@/mocks/node'
