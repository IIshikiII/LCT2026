/**
 * Форматтеры.
 *
 * Все принимают `unknown` и возвращают прочерк на всём, что не разобралось.
 * Это прямое следствие ADR 0006: разбор ответов не бросает, значит поле,
 * объявленное как number, во время выполнения может оказаться чем угодно.
 * Ячейка с прочерком лучше белого экрана.
 */

export const DASH = '—'

function num(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function date(value: unknown): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  if (typeof value !== 'string' || !value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/** 0.734 → «73 %». Вероятность всегда в процентах, дробей не показываем. */
export function fmtPct(value: unknown, digits = 0): string {
  const n = num(value)
  if (n === null) return DASH
  return `${(n * 100).toFixed(digits).replace('.', ',')} %`
}

export function fmtNumber(value: unknown, digits = 0): string {
  const n = num(value)
  if (n === null) return DASH
  return n.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** 0.712 → «0,71». Для Precision и Recall. */
export function fmtScore(value: unknown, digits = 2): string {
  const n = num(value)
  if (n === null) return DASH
  return n.toFixed(digits).replace('.', ',')
}

const dateTimeFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

const dateFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
})

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })

const dayMonthFmt = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit' })

export function fmtDateTime(value: unknown): string {
  const d = date(value)
  return d ? dateTimeFmt.format(d).replace(', ', ' ') : DASH
}

export function fmtDate(value: unknown): string {
  const d = date(value)
  return d ? dateFmt.format(d) : DASH
}

export function fmtTime(value: unknown): string {
  const d = date(value)
  return d ? timeFmt.format(d) : DASH
}

/** Число и месяц без года: подпись деления на оси длинного графика. */
export function fmtDayMonth(value: unknown): string {
  const d = date(value)
  return d ? dayMonthFmt.format(d) : DASH
}

/** Ряд длиннее этого подписывает деления оси датами, короче — временем. */
const AXIS_DATES_AFTER_MS = 36 * 3_600_000

/**
 * Формат делений оси времени по длине ряда. Суточный ряд за месяц с подписями
 * «12:00, 00:33» читался бы как часы одних суток.
 */
export function axisTimeFormatter(spanMs: number): (value: unknown) => string {
  return spanMs > AXIS_DATES_AFTER_MS ? fmtDayMonth : fmtTime
}

/** Русское склонение: plural(5, ['час','часа','часов']) → 'часов'. */
export function plural(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(n) % 100
  const tail = abs % 10
  if (abs > 10 && abs < 20) return forms[2]
  if (tail > 1 && tail < 5) return forms[1]
  if (tail === 1) return forms[0]
  return forms[2]
}

/** 36 → «36 ч». Горизонт прогноза. */
export function fmtHours(value: unknown): string {
  const n = num(value)
  return n === null ? DASH : `${fmtNumber(n)} ч`
}

/** 138000 → «2 мин 18 с». Время расчёта прогноза. */
export function fmtDuration(ms: unknown): string {
  const n = num(ms)
  if (n === null) return DASH
  if (n < 1000) return `${Math.round(n)} мс`
  const totalSeconds = Math.round(n / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes === 0) return `${seconds} с`
  return seconds === 0 ? `${minutes} мин` : `${minutes} мин ${seconds} с`
}

/** 7 → «7 минут назад». Свежесть данных в шапке. */
export function fmtFreshness(minutes: unknown): string {
  const n = num(minutes)
  if (n === null) return DASH
  const m = Math.max(0, Math.round(n))
  if (m === 0) return 'только что'
  if (m < 60) return `${m} ${plural(m, ['минута', 'минуты', 'минут'])} назад`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} ${plural(h, ['час', 'часа', 'часов'])} назад`
  const d = Math.floor(h / 24)
  return `${d} ${plural(d, ['день', 'дня', 'дней'])} назад`
}

/**
 * Срок заявки относительно момента `now`: «через 2 дня» / «просрочено на 3 часа».
 * `now` передаётся явно, чтобы тесты не зависели от системных часов.
 */
export function fmtDueIn(value: unknown, now: Date = new Date()): string {
  const d = date(value)
  if (!d) return DASH
  const diffMinutes = Math.round((d.getTime() - now.getTime()) / 60_000)
  const overdue = diffMinutes < 0
  const abs = Math.abs(diffMinutes)
  const body =
    abs < 60
      ? `${abs} ${plural(abs, ['минуту', 'минуты', 'минут'])}`
      : abs < 60 * 24
        ? `${Math.floor(abs / 60)} ${plural(Math.floor(abs / 60), ['час', 'часа', 'часов'])}`
        : `${Math.floor(abs / 1440)} ${plural(Math.floor(abs / 1440), ['день', 'дня', 'дней'])}`
  return overdue ? `просрочено на ${body}` : `через ${body}`
}

export function isOverdue(value: unknown, now: Date = new Date()): boolean {
  const d = date(value)
  return d ? d.getTime() < now.getTime() : false
}

/** Иерархия объекта одной строкой: «Коллектор 3 · Участок 12 · Камера 4». */
export function fmtFacilityPath(facility: {
  collector?: string
  section?: string
  chamber?: string
  device?: string
}): string {
  const parts = [facility.collector, facility.section, facility.chamber, facility.device].filter(
    (p): p is string => Boolean(p),
  )
  return parts.length ? parts.join(' · ') : DASH
}

/** Значение произвольного типа в текст — для GenericBlock и TableBlock. */
export function fmtUnknown(value: unknown): string {
  if (value === null || value === undefined || value === '') return DASH
  if (typeof value === 'boolean') return value ? 'да' : 'нет'
  if (typeof value === 'number') return fmtNumber(value, Number.isInteger(value) ? 0 : 2)
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}
