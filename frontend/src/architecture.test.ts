/**
 * Архитектурный тест: читает исходники и падает, если нарушен главный принцип
 * проекта (ADR 0002, ADR 0007).
 *
 * Зачем это тестом, а не устной договорённостью: за неделю интенсивной
 * разработки принцип «направление — это строка» размывается первым же
 * «ну здесь-то один раз можно». Дешёвая проверка держит границу.
 *
 * Комментарии из исходников вырезаются перед проверкой: пояснение «датчик,
 * камера, вентшахта» в документирующем комментарии — это документация, а не
 * доменная логика в UI.
 *
 * Если тест мешает — значит, либо правка неверна, либо принцип пора обсуждать
 * явно и менять ADR, а не обходить проверку.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) return walk(full)
    return /\.tsx?$/.test(entry) ? [full] : []
  })
}

/** Убирает комментарии и строковые литералы: проверяем код, а не тексты. */
function stripNoise(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

const files = walk(SRC).map((path) => ({
  path,
  rel: relative(SRC, path).split(sep).join('/'),
  code: stripNoise(readFileSync(path, 'utf8')),
}))

const appFiles = files.filter((f) => !f.rel.startsWith('mocks/') && !f.rel.endsWith('.test.ts') && !f.rel.endsWith('.test.tsx'))

describe('исходники вообще читаются', () => {
  it('находит заметное число файлов — иначе тест ничего не проверяет', () => {
    expect(files.length).toBeGreaterThan(30)
    expect(appFiles.length).toBeGreaterThan(20)
  })
})

describe('ADR 0002: направление, уровень и статус — строки, а не union', () => {
  it('не заводит union-тип по кодам предметной области', () => {
    const bad = files.filter((f) =>
      /type\s+\w*(Direction|RiskLevel|Level|Status)\w*\s*=\s*(\|\s*)?['"]/.test(f.code),
    )
    expect(bad.map((f) => f.rel)).toEqual([])
  })

  it('нигде не ветвится switch по коду направления', () => {
    const bad = files.filter((f) => /switch\s*\([^)]*\bdirection\b/i.test(f.code))
    expect(bad.map((f) => f.rel)).toEqual([])
  })

  it('не содержит switch по уровню риска или статусу', () => {
    const bad = files.filter((f) => /switch\s*\([^)]*\b(level|status)\b/i.test(f.code))
    expect(bad.map((f) => f.rel)).toEqual([])
  })
})

describe('ADR 0007: предметная область живёт только в заглушках', () => {
  const DOMAIN_WORDS = [
    'пожарн',
    'датчик',
    'несанкционир',
    'задымлен',
    'сварочн',
    'вентшахт',
    'допуск',
  ]

  it('не упоминает направления прогнозирования вне src/mocks/', () => {
    const offenders = appFiles
      .map((file) => ({
        rel: file.rel,
        words: DOMAIN_WORDS.filter((word) => file.code.toLowerCase().includes(word)),
      }))
      .filter((entry) => entry.words.length > 0)

    expect(offenders).toEqual([])
  })

  it('не содержит захардкоженных кодов направлений вне src/mocks/', () => {
    const bad = appFiles.filter((f) =>
      /['"](FIRE_RISK|SENSOR_FAILURE|UNAUTHORIZED_ACCESS|FLOOD_RISK)['"]/.test(f.code),
    )
    expect(bad.map((f) => f.rel)).toEqual([])
  })
})

describe('ADR 0007: заглушки изолированы', () => {
  /**
   * Единственные файлы приложения, которым позволено знать о заглушках.
   * Тесты в список не входят: им обращаться к заглушкам можно и нужно —
   * проверка охраняет продакшен-код, а не тестовый.
   */
  const ALLOWED = ['main.tsx', 'test/msw.ts', 'test/setup.ts']

  it('никто вне разрешённого списка не импортирует из src/mocks/', () => {
    const bad = appFiles.filter((file) => {
      if (ALLOWED.includes(file.rel)) return false
      return /(from|import\()\s*['"](@\/mocks|\.\.?\/(\.\.\/)*mocks)/.test(file.code)
    })
    expect(bad.map((f) => f.rel)).toEqual([])
  })
})

describe('прочие договорённости', () => {
  it('не тянет в проект глобальный стор', () => {
    const bad = files.filter((f) => /from\s+['"](zustand|redux|@reduxjs|jotai|mobx)/.test(f.code))
    expect(bad.map((f) => f.rel)).toEqual([])
  })

  it('не открывает WebSocket — горизонт 24 часа этого не требует (ADR 0005)', () => {
    const bad = files.filter((f) => /new\s+WebSocket|EventSource\(/.test(f.code))
    expect(bad.map((f) => f.rel)).toEqual([])
  })

  it('вызывает fetch только в http-клиенте', () => {
    const bad = files.filter(
      (f) => /(?<!\w)fetch\s*\(/.test(f.code) && f.rel !== 'shared/api/client.ts',
    )
    expect(bad.map((f) => f.rel)).toEqual([])
  })
})
