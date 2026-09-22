/**
 * QR-код. Проверяется то, от чего зависит читаемость телефоном.
 *
 * Правильность самой матрицы проверять нечем: эталонов у нас нет, а
 * пересчитывать её вторым кодировщиком значит проверять библиотеку ею же.
 * Поэтому здесь проверяются свойства, которые ломаются от правки вёрстки:
 * светлый фон, поле тишины по краю, доступная подпись, рост под длину строки.
 */
import { cleanup, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { QrCode } from './QrCode'

const OTPAUTH =
  'otpauth://totp/%D0%90%D0%A0%D0%9C%20%D0%9E%D0%94%D0%A1%3Aods' +
  '?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=test&algorithm=SHA1&digits=6&period=30'

/** Рисует код и отдаёт его svg. Каждый вызов начинает с чистого DOM. */
function draw(value: string): SVGSVGElement {
  cleanup()
  const { container } = render(<QrCode value={value} label="код" />)
  const svg = container.querySelector('svg')
  if (!svg) throw new Error('QrCode не нарисовал svg')
  return svg
}

/** Сторона картинки в модулях, включая поле тишины. */
function side(svg: SVGSVGElement): number {
  return Number(svg.getAttribute('viewBox')?.split(' ')[2])
}

/** Координаты левых верхних углов тёмных модулей. */
function darkModules(svg: SVGSVGElement): { x: number; y: number }[] {
  const path = svg.querySelector('path')?.getAttribute('d') ?? ''
  return [...path.matchAll(/M(\d+) (\d+)/g)].map((match) => ({
    x: Number(match[1]),
    y: Number(match[2]),
  }))
}

describe('QR-код', () => {
  it('рисует картинку с доступной подписью', () => {
    expect(draw(OTPAUTH).getAttribute('aria-label')).toBe('код')
  })

  it('держит светлый фон независимо от темы', () => {
    // Сканеру нужен контраст. Тёмный фон темы сделал бы код нечитаемым.
    expect(draw(OTPAUTH).querySelector('rect')).toHaveAttribute('fill', '#ffffff')
  })

  it('оставляет поле тишины со всех четырёх сторон', () => {
    // Без него телефон не находит границы кода.
    const svg = draw(OTPAUTH)
    const modules = darkModules(svg)
    const edge = side(svg)

    expect(Math.min(...modules.map((m) => m.x))).toBeGreaterThanOrEqual(4)
    expect(Math.min(...modules.map((m) => m.y))).toBeGreaterThanOrEqual(4)
    expect(Math.max(...modules.map((m) => m.x))).toBeLessThanOrEqual(edge - 5)
    expect(Math.max(...modules.map((m) => m.y))).toBeLessThanOrEqual(edge - 5)
  })

  it('берёт версию под длину строки', () => {
    // Короткая строка — маленькая матрица, длинная — больше. Иначе длинная
    // ссылка молча не влезла бы.
    const small = side(draw('короткая'))
    const large = side(draw(OTPAUTH + '&extra=' + 'x'.repeat(300)))

    expect(large).toBeGreaterThan(small)
  })

  it('рисует тёмные модули одним путём', () => {
    const path = draw(OTPAUTH).querySelector('path')

    expect(path).toHaveAttribute('fill', '#000000')
    expect(darkModules(draw(OTPAUTH)).length).toBeGreaterThan(100)
  })
})
