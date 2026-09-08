import { expect, test } from 'vitest'

// Проверка, что тестовый рантайм (vitest + jsdom + jest-dom) поднимается.
// Удалить, когда появятся настоящие тесты.
test('toolchain is alive', () => {
  const el = document.createElement('div')
  el.textContent = 'ok'
  expect(el).toHaveTextContent('ok')
})
