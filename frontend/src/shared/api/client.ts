/**
 * HTTP-клиент. Единственное место, где вызывается fetch.
 *
 * Ключевое поведение — разбор не бросает исключений (ADR 0006). Схема служит
 * описанием ожиданий и точкой наблюдения: если ответ не совпал, мы громко пишем
 * в консоль и возвращаем данные как есть. Белый экран из-за переименованного
 * поля недопустим, особенно на защите.
 *
 * Бросаем только на транспортных ошибках: сеть, код ответа >= 400, невалидный
 * JSON. Их ловит TanStack Query и рисует ErrorState с кнопкой «повторить».
 *
 * Токен подставляется здесь же. Ответ 401 значит, что сессия кончилась: клиент
 * снимает её, и каркас приложения показывает экран входа. Без этого истёкший
 * токен давал бы экран ошибки с кнопкой «повторить», которая не помогает.
 */
import type { ZodType } from 'zod'
import { clearSession, currentToken } from '@/shared/auth/session'
import { env } from '@/shared/config/env'

export type QueryValue = string | number | boolean | string[] | undefined | null
export type QueryParams = Record<string, QueryValue>

export class ApiError extends Error {
  status: number
  url: string

  constructor(message: string, status: number, url: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.url = url
  }
}

/**
 * Собирает строку запроса.
 *
 * Два инварианта, от которых зависит стабильность ключей кэша (docs/06-url-state.md):
 *  - пустые значения выбрасываются, а не пишутся как `?district=`;
 *  - повторяемые значения сортируются, чтобы ?level=HIGH&level=LOW и
 *    ?level=LOW&level=HIGH давали один и тот же запрос.
 */
export function buildQuery(params?: QueryParams): string {
  if (!params) return ''
  const sp = new URLSearchParams()
  for (const key of Object.keys(params).sort()) {
    const value = params[key]
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const item of [...value].filter(Boolean).sort()) sp.append(key, item)
    } else {
      sp.append(key, String(value))
    }
  }
  const qs = sp.toString()
  return qs ? `?${qs}` : ''
}

/**
 * Разбор, который никогда не бросает.
 * Не прошло — предупреждение в консоль и данные как есть.
 */
export function parseTolerant<T>(schema: ZodType, raw: unknown, url: string): T {
  const result = schema.safeParse(raw)
  if (result.success) return result.data as T
  console.warn(
    `[api] ответ ${url} не совпал с ожидаемой схемой — работаем с данными как есть`,
    result.error.issues,
  )
  return raw as T
}

/**
 * Текст ошибки для человека.
 *
 * Сервер объясняет отказ полем `detail`: «Логин или пароль не подошли», «роль
 * не выполняет действие», «горизонт уже истёк». Показывать вместо этого код
 * ответа значит прятать единственное, что помогает исправить ситуацию.
 *
 * Тело не разобралось — остаётся код. Так бывает на ответе прокси или
 * шлюза, который про наш формат ничего не знает.
 */
function errorMessage(text: string, status: number): string {
  try {
    const body = JSON.parse(text) as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail.trim()) return body.detail
  } catch {
    // Не JSON. Ниже вернётся код ответа.
  }
  return `Запрос завершился с кодом ${status}`
}

/** Заголовки запроса: тип тела и токен сессии, если он есть. */
function authHeaders(hasBody: boolean): Record<string, string> | undefined {
  const headers: Record<string, string> = {}
  if (hasBody) headers['Content-Type'] = 'application/json'
  const token = currentToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  return Object.keys(headers).length > 0 ? headers : undefined
}

async function request<T>(
  method: 'GET' | 'POST',
  path: string,
  schema: ZodType,
  options: { params?: QueryParams; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const url = `${env.apiBaseUrl}${path}${buildQuery(options.params)}`

  const response = await fetch(url, {
    method,
    signal: options.signal,
    headers: authHeaders(options.body !== undefined),
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })

  // Тело вычитывается ВСЕГДА, в том числе у ответов с ошибкой. Иначе соединение
  // остаётся незакрытым и следующие параллельные запросы к тому же хосту
  // подвисают — на дежурном экране это выглядит как намертво зависший интерфейс.
  const text = await response.text()

  if (!response.ok) {
    if (response.status === 401) clearSession()
    throw new ApiError(errorMessage(text, response.status), response.status, url)
  }

  if (!text) return parseTolerant<T>(schema, null, url)

  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    throw new ApiError('Ответ не является корректным JSON', response.status, url)
  }

  return parseTolerant<T>(schema, raw, url)
}

export function apiGet<T>(
  path: string,
  schema: ZodType,
  options: { params?: QueryParams; signal?: AbortSignal } = {},
): Promise<T> {
  return request<T>('GET', path, schema, options)
}

export function apiPost<T>(
  path: string,
  schema: ZodType,
  body: unknown,
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  return request<T>('POST', path, schema, { ...options, body })
}
