/**
 * Тем две, набор имён у них общий (ADR 0009).
 *
 * Проверка читает сам tokens.css, а не вычисленные стили: jsdom не считает
 * каскад, да и ошибка тут именно текстовая — забыли дописать новый токен во
 * вторую тему. Тогда в светлой теме он молча возьмётся из :root, то есть
 * останется тёмным, и заметят это в лучшем случае глазами на демонстрации.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(process.cwd(), 'src/styles/tokens.css'), 'utf8')

/** Тело блока по его селектору. */
function block(selector: string): string {
  const start = css.indexOf(selector)
  if (start === -1) throw new Error(`В tokens.css нет блока ${selector}`)
  const open = css.indexOf('{', start)
  const close = css.indexOf('}', open)
  return css.slice(open + 1, close)
}

/** Имена объявленных переменных и их значения. */
function declarations(body: string): Map<string, string> {
  const found = new Map<string, string>()
  for (const match of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    found.set(match[1]!, match[2]!.trim())
  }
  return found
}

const dark = declarations(block(':root {'))
const light = declarations(block(":root[data-theme='light']"))

describe('токены темы', () => {
  it('вообще разбираются', () => {
    expect(dark.size).toBeGreaterThan(15)
    expect(light.size).toBeGreaterThan(10)
  })

  it('каждый цвет тёмной темы переопределён в светлой', () => {
    // Цветом считаем то, что записано шестнадцатеричным литералом: шрифты и
    // радиус от темы не зависят и дублироваться не должны.
    const colors = [...dark].filter(([, value]) => value.startsWith('#')).map(([name]) => name)
    const missing = colors.filter((name) => !light.has(name))
    expect(missing).toEqual([])
  })

  it('светлая тема не заводит токенов, которых нет в тёмной', () => {
    const extra = [...light.keys()].filter((name) => !dark.has(name))
    expect(extra).toEqual([])
  })

  it('светлая тема действительно светлее: фон ярче текста', () => {
    const luminance = (hex: string) => {
      const value = hex.replace('#', '')
      const [r, g, b] = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16))
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!
    }
    expect(luminance(light.get('--bg')!)).toBeGreaterThan(luminance(light.get('--text')!))
    expect(luminance(dark.get('--bg')!)).toBeLessThan(luminance(dark.get('--text')!))
  })
})
