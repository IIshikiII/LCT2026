/**
 * QR-код как SVG.
 *
 * Нужен ровно в одном месте: экран регистрации ключа второго фактора. Телефон
 * наводят на код, и аутентификатор заводит запись сам. Ручной ввод ключа рядом
 * остаётся: это запасной путь, когда камера не берёт.
 *
 * **Цвета фиксированные и не зависят от темы.** Сканеру нужен контраст и
 * светлый фон, поэтому код всегда чёрный на белом, даже в тёмной теме. Поле
 * тишины по краю обязательно, иначе телефон не находит границы кода.
 *
 * Матрицу считает `qrcode-generator` (MIT, Kazuhiko Arase). Свой кодировщик
 * занял бы 350 строк: поле Галуа, Рида-Соломона, выбор версии и восемь масок
 * со штрафами. Ошибка в нём проявилась бы не падением, а кодом, который не
 * читает телефон.
 */
import qrcode from 'qrcode-generator'
import { cn } from '@/shared/lib/cn'

/**
 * Поле тишины по краю, в модулях. Стандарт требует четыре.
 */
const QUIET_ZONE = 4

/**
 * Уровень коррекции. `M` держит потерю до 15 % и даёт код умеренного размера.
 * Ссылка `otpauth://` занимает около 120 знаков, и на `M` это 45 модулей.
 */
const ERROR_CORRECTION = 'M'

export interface QrCodeProps {
  /** Что кодируем. Для второго фактора это ссылка `otpauth://`. */
  value: string
  /**
   * Сторона картинки в пикселях. По умолчанию 200: ссылка `otpauth://` даёт
   * 45 модулей, и это без малого четыре пикселя на модуль. Меньше трёх
   * телефон берёт с экрана монитора через раз.
   */
  size?: number
  /** Подпись для экранного диктора. */
  label?: string
  className?: string
}

/** Собирает один путь из тёмных модулей: по прямоугольнику на модуль. */
function modulesPath(code: ReturnType<typeof qrcode>, count: number): string {
  const parts: string[] = []
  for (let row = 0; row < count; row += 1) {
    for (let column = 0; column < count; column += 1) {
      if (code.isDark(row, column)) {
        parts.push(`M${column + QUIET_ZONE} ${row + QUIET_ZONE}h1v1h-1z`)
      }
    }
  }
  return parts.join('')
}

export function QrCode({ value, size = 200, label, className }: QrCodeProps) {
  const code = qrcode(0, ERROR_CORRECTION)
  code.addData(value)
  code.make()

  const count = code.getModuleCount()
  const side = count + QUIET_ZONE * 2

  return (
    <svg
      role="img"
      aria-label={label ?? 'QR-код'}
      width={size}
      height={size}
      viewBox={`0 0 ${side} ${side}`}
      shapeRendering="crispEdges"
      className={cn('rounded', className)}
    >
      <rect width={side} height={side} fill="#ffffff" />
      <path d={modulesPath(code, count)} fill="#000000" />
    </svg>
  )
}
