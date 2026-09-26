/**
 * Когда показать уведомление о тревоге и когда напомнить снова.
 *
 * Правила (ТЗ §10, ADR 0019):
 * 1. Новая тревога показывается сразу.
 * 2. Инцидентов стало больше — показывается сразу, даже если её скрыли.
 * 3. Скрытая тревога возвращается через `repeatMinutes` после скрытия.
 * 4. Видимая тревога через тот же шаг напоминает о себе: `pulse` растёт, и
 *    уведомление мигает рамкой.
 * 5. Тревоги нет в ответе сервера — память о ней стирается, и следующая такая
 *    тревога снова считается новой.
 *
 * Функции чистые: время приходит аргументом, поэтому правила проверяются тестом
 * без таймеров.
 */

export interface AlertMemory {
  /** когда уведомление показано или скрыто в последний раз, мс */
  at: number
  count: number
  hidden: boolean
  pulse: number
}

export function remember(
  prev: AlertMemory | undefined,
  count: number,
  repeatMinutes: number,
  now: number,
): AlertMemory {
  const repeatMs = repeatMinutes * 60_000
  if (!prev) return { at: now, count, hidden: false, pulse: 0 }
  if (count > prev.count) return { at: now, count, hidden: false, pulse: prev.pulse + 1 }
  if (now - prev.at >= repeatMs) return { at: now, count, hidden: false, pulse: prev.pulse + 1 }
  return { ...prev, count }
}

export function hide(memory: AlertMemory, now: number): AlertMemory {
  return { ...memory, at: now, hidden: true }
}

/** Параметры журнала, которые показывают инциденты тревоги. */
export function journalLink(filter: Record<string, string[]>): string {
  const params = new URLSearchParams()
  for (const [key, values] of Object.entries(filter)) {
    for (const value of values) params.append(key, value)
  }
  return `/journal?${params.toString()}`
}
