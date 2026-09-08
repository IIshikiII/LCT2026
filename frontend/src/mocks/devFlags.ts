/**
 * Переключатели дев-панели. Только для заглушек: в сборке с реальным бэкендом
 * этот модуль не участвует в работе приложения.
 *
 * Хранятся в localStorage, чтобы состояние переживало перезагрузку страницы во
 * время демонстрации. Чтение обёрнуто в try — в средах без localStorage
 * (некоторые тестовые прогоны) флаги просто выключены.
 */
export interface DevFlags {
  /** GET /meta отвечает 500 — приложение должно подняться на FALLBACK_META. */
  breakMeta: boolean
  /** Добавить пятое направление: проверка гибкости архитектуры вживую. */
  extraDirection: boolean
  /** Задержка ответов 2–4 с — проверка скелетов. */
  slowNetwork: boolean
  /** Списочные ручки отвечают 500 — проверка ErrorState. */
  failLists: boolean
}

const STORAGE_KEY = 'arm-ods.dev-flags'

const defaults: DevFlags = {
  breakMeta: false,
  extraDirection: false,
  slowNetwork: false,
  failLists: false,
}

/** Значения, выставленные программно (тесты). Имеют приоритет над хранилищем. */
let overrides: Partial<DevFlags> = {}

function read(): DevFlags {
  let stored: Partial<DevFlags> = {}
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (raw) stored = JSON.parse(raw) as Partial<DevFlags>
  } catch {
    stored = {}
  }
  return { ...defaults, ...stored, ...overrides }
}

export const devFlags = {
  all: read,
  get: (key: keyof DevFlags): boolean => read()[key],

  set(key: keyof DevFlags, value: boolean): void {
    const next = { ...read(), [key]: value }
    try {
      globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // Хранилище недоступно — работаем на overrides.
      overrides = { ...overrides, [key]: value }
    }
  },

  /** Для тестов: выставить флаги в обход хранилища и сбросить обратно. */
  override(values: Partial<DevFlags>): void {
    overrides = values
  },

  reset(): void {
    overrides = {}
    try {
      globalThis.localStorage?.removeItem(STORAGE_KEY)
    } catch {
      // ничего
    }
  },
}
