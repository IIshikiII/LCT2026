/**
 * HTTP-хендлеры MSW: по одному на каждую ручку из docs/02-api-contract.md.
 *
 * Это стенд-ин бэкенда, а не тестовые фикстуры (ADR 0007): те же хендлеры
 * обслуживают и браузер, и Vitest. Добавил ручку в endpoints.ts — добавь
 * хендлер сюда, иначе VITE_USE_MOCKS=true перестанет быть правдой.
 */
import { HttpResponse, delay, http } from 'msw'
import type {
  ActionDef,
  CurrentUser,
  DashboardSummary,
  FacilityFeature,
  Prediction,
} from '@/shared/api/types'
import { db, resetDb, type OrderRecord, type PredictionRecord } from './db'
import { orderActions } from './db/actions'
import { COLLECTORS, collectorLabel } from './db/catalog'
import { toWorkOrder } from './db/orders'
import { buildDetail, buildSeries } from './db/predictions'
import {
  CODE_LENGTH,
  DEMO_PASSWORD,
  resetUsers,
  userByName,
  visibleTo,
  type MockUser,
} from './db/users'
import { devFlags } from './devFlags'

/** Базовый путь. Звёздочка — чтобы работало и на относительном URL, и на полном. */
const url = (path: string) => `*/api/v1${path}`

/** В тестах задержки не нужны: они превращают каждый it в ожидание. */
const noDelay = import.meta.env.MODE === 'test'

async function networkDelay(): Promise<void> {
  if (noDelay) return
  await delay(devFlags.get('slowNetwork') ? 2000 + Math.random() * 2000 : 150 + Math.random() * 350)
}

async function ok(payload: unknown): Promise<Response> {
  await networkDelay()
  return HttpResponse.json(payload as never)
}

async function fail(status: number, message: string): Promise<Response> {
  await networkDelay()
  return HttpResponse.json({ detail: message }, { status })
}

/* ------------------------------------------------------------------ вход */

/**
 * Токен заглушки. Подписи нет и быть не может: подписывать нечем, а разбирать
 * его будет тот же процесс. Формат оставлен читаемым намеренно — в отладчике
 * видно, под кем идёт запрос.
 */
const tokenFor = (username: string) => `mock.access.${username}`
const mfaTokenFor = (username: string) => `mock.mfa.${username}`

/** Постоянный ключ для демонстрации регистрации второго фактора. */
const MOCK_TOTP_SECRET = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'

const DIGITS = /^\d+$/

// Латиница ради размера QR: кириллица в ссылке кодируется по шесть знаков на
// букву и раздувает код с 45 модулей до 57. То же значение у сервера.
const ISSUER = 'ARM ODS'

/** Ссылка для аутентификатора. Формат тот же, что у сервера. */
function otpauthUrl(username: string): string {
  const label = encodeURIComponent(`${ISSUER}:${username}`)
  return (
    `otpauth://totp/${label}?secret=${MOCK_TOTP_SECRET}` +
    `&issuer=${encodeURIComponent(ISSUER)}&algorithm=SHA1&digits=${CODE_LENGTH}&period=30`
  )
}

function toCurrentUser(user: MockUser): CurrentUser {
  return {
    username: user.username,
    fullName: user.fullName,
    role: user.role,
    roleLabel: user.roleLabel,
    scopeKind: user.scopeKind,
    scopeValue: user.scopeValue,
    permissions: user.permissions,
  }
}

function nameFromToken(prefix: string, token: string | null): string | null {
  if (!token || !token.startsWith(prefix)) return null
  return token.slice(prefix.length) || null
}

/** Кто выполняет запрос. `null` значит «токена нет или он не тот». */
function actorOf(request: Request): MockUser | null {
  const header = request.headers.get('Authorization') ?? ''
  const token = header.startsWith('Bearer ') ? header.slice('Bearer '.length) : null
  const username = nameFromToken('mock.access.', token)
  return username ? (userByName(username) ?? null) : null
}

/**
 * Оборачивает хендлер проверкой токена.
 *
 * Заглушка отказывает так же, как сервер: 401 без токена. Иначе экран входа
 * можно было бы обойти, и `VITE_USE_MOCKS=true` перестал бы быть правдой.
 */
function guarded(
  handler: (user: MockUser, input: { request: Request; params: Record<string, string> }) => Promise<Response>,
) {
  return async (input: { request: Request; params: unknown }): Promise<Response> => {
    const user = actorOf(input.request)
    if (!user) return fail(401, 'Требуется вход в систему')
    return handler(user, {
      request: input.request,
      params: (input.params ?? {}) as Record<string, string>,
    })
  }
}

/** Оставляет действия, на которые у роли есть право. */
function allowed(actions: ActionDef[], user: MockUser): ActionDef[] {
  return actions.filter((action) => user.permissions.includes(action.code))
}

function seesPrediction(user: MockUser, record: PredictionRecord): boolean {
  return visibleTo(user, record.facility.district, record.facility.collector)
}

function seesOrder(user: MockUser, order: OrderRecord): boolean {
  return visibleTo(user, order.facility.district, order.facility.collector)
}

/** Карточка прогноза с кнопками, отобранными по правам роли. */
function detailFor(record: PredictionRecord, user: MockUser) {
  const detail = buildDetail(record)
  return { ...detail, actions: allowed(detail.actions, user) }
}

/** Заявка с кнопками, отобранными по правам роли. */
function orderFor(order: OrderRecord, user: MockUser) {
  const dto = toWorkOrder(order)
  return { ...dto, actions: allowed(dto.actions, user) }
}

/* ------------------------------------------------------- выборка и разбор */

function listParam(request: Request, name: string): string[] {
  return new URL(request.url).searchParams.getAll(name).filter(Boolean)
}

function param(request: Request, name: string): string | undefined {
  return new URL(request.url).searchParams.get(name) ?? undefined
}

/** Границы видимой области карты: `minLon,minLat,maxLon,maxLat`. */
type Bbox = [number, number, number, number]

/**
 * Разбирает bbox. Мусор в параметре — не ошибка: карта просто получит все
 * объекты, а не пустой экран.
 */
function parseBbox(value: string | undefined): Bbox | undefined {
  if (!value) return undefined
  const parts = value.split(',').map(Number)
  if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return undefined
  return parts as Bbox
}

function insideBbox([minLon, minLat, maxLon, maxLat]: Bbox, lon: number, lat: number): boolean {
  return lon >= minLon && lon <= maxLon && lat >= minLat && lat <= maxLat
}

function matchesFilters(record: PredictionRecord, request: Request): boolean {
  const direction = listParam(request, 'direction')
  const level = listParam(request, 'level')
  const status = listParam(request, 'status')
  const district = param(request, 'district')
  const from = param(request, 'from')
  const to = param(request, 'to')

  if (direction.length && !direction.includes(record.direction)) return false
  if (level.length && !level.includes(record.level)) return false
  if (status.length && !status.includes(record.status)) return false
  if (district && record.facility.district !== district) return false
  if (from && record.computedAt < from) return false
  if (to && record.computedAt > `${to}T23:59:59.999Z`) return false
  return true
}

/**
 * Сортировка по ключу колонки. Ключи те же, что в columnRegistry, — иначе
 * заголовок таблицы сортировал бы не то, что показывает.
 */
const predictionSortValue: Record<string, (p: PredictionRecord) => string | number> = {
  computedAt: (p) => p.computedAt,
  probability: (p) => p.probability,
  horizon: (p) => p.horizonHours,
  direction: (p) => p.direction,
  status: (p) => p.status,
  facility: (p) => p.facility.address,
  summary: (p) => p.summary,
  risk: (p) => p.probability,
}

const orderSortValue: Record<string, (o: OrderRecord) => string | number> = {
  number: (o) => o.number,
  dueAt: (o) => o.dueAt,
  orderStatus: (o) => o.status,
  workType: (o) => o.workType,
  facility: (o) => o.facility.address,
}

function applySort<T>(
  items: T[],
  sort: string | undefined,
  accessors: Record<string, (item: T) => string | number>,
): T[] {
  if (!sort) return items
  const [field, direction] = sort.split(':')
  const accessor = field ? accessors[field] : undefined
  if (!accessor) return items
  const sign = direction === 'asc' ? 1 : -1
  return [...items].sort((a, b) => {
    const va = accessor(a)
    const vb = accessor(b)
    if (va === vb) return 0
    return va > vb ? sign : -sign
  })
}

function paginate<T>(items: T[], request: Request) {
  const page = Math.max(1, Number(param(request, 'page') ?? '1') || 1)
  const pageSize = Math.max(1, Number(param(request, 'pageSize') ?? '50') || 50)
  const start = (page - 1) * pageSize
  return { items: items.slice(start, start + pageSize), page, pageSize, total: items.length }
}

/** Прогноз в том виде, в каком его отдаёт API: без служебных полей сида. */
function toPrediction(record: PredictionRecord): Prediction {
  const { plugin: _plugin, facilityRecord: _facility, ...rest } = record
  return rest
}

/* ---------------------------------------------------------------- ручки */

export const handlers = [
  /* вход в систему */

  /**
   * Первый шаг. Пароль проверяется, потому что экран входа обязан отказывать.
   * Ответ говорит, что делать дальше: прислать код или сначала завести ключ.
   */
  http.post(url('/auth/login'), async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>
    const user = userByName(String(body['username'] ?? ''))
    if (!user || body['password'] !== DEMO_PASSWORD) {
      // Ответ один на оба случая: разные подсказки дали бы перебор имён.
      return fail(401, 'Логин или пароль не подошли')
    }

    if (!user.enrolled) {
      return ok({
        status: 'ENROLL_REQUIRED',
        mfaToken: mfaTokenFor(user.username),
        secret: MOCK_TOTP_SECRET,
        otpauthUrl: otpauthUrl(user.username),
      })
    }

    return ok({ status: 'MFA_REQUIRED', mfaToken: mfaTokenFor(user.username) })
  }),

  /**
   * Второй шаг. Код не считается: общего секрета с настоящим аутентификатором
   * у заглушки нет. Подходят любые шесть цифр, всё остальное — отказ.
   */
  http.post(url('/auth/mfa'), async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>
    const username = nameFromToken('mock.mfa.', String(body['mfaToken'] ?? ''))
    const user = username ? userByName(username) : undefined
    if (!user) return fail(401, 'Время на ввод кода истекло, войдите заново')

    const code = String(body['code'] ?? '').trim()
    if (code.length !== CODE_LENGTH || !DIGITS.test(code)) {
      return fail(401, 'Код не подошёл. Проверьте время на телефоне и повторите')
    }

    // Ключ заведён: следующий вход этой записи пойдёт сразу к коду.
    user.enrolled = true

    return ok({
      accessToken: tokenFor(user.username),
      tokenType: 'Bearer',
      expiresIn: 8 * 60 * 60,
      user: toCurrentUser(user),
    })
  }),

  http.get(url('/auth/me'), guarded(async (user) => ok(toCurrentUser(user)))),

  http.post(url('/auth/logout'), guarded(async () => ok({ status: 'ok' }))),

  /* мета */
  http.get(
    url('/meta'),
    guarded(async () => {
      if (devFlags.get('breakMeta')) {
        return fail(500, 'Дев-панель: /meta намеренно сломана')
      }
      return ok(db().meta)
    }),
  ),

  /* прогнозы */
  http.get(
    url('/predictions'),
    guarded(async (user, { request }) => {
      if (devFlags.get('failLists')) return fail(500, 'Дев-панель: списки намеренно падают')

      const filtered = db().predictions.filter(
        (p) => seesPrediction(user, p) && matchesFilters(p, request),
      )
      const sorted = applySort(filtered, param(request, 'sort'), predictionSortValue)
      const page = paginate(sorted, request)
      return ok({ ...page, items: page.items.map(toPrediction) })
    }),
  ),

  http.get(
    url('/predictions/:id/timeseries'),
    guarded(async (user, { params }) => {
      const record = db().predictionById.get(String(params['id']))
      if (!record || !seesPrediction(user, record)) return fail(404, 'Прогноз не найден')
      return ok(buildSeries(record))
    }),
  ),

  http.get(
    url('/predictions/:id'),
    guarded(async (user, { params }) => {
      const record = db().predictionById.get(String(params['id']))
      if (!record || !seesPrediction(user, record)) return fail(404, 'Прогноз не найден')
      return ok(detailFor(record, user))
    }),
  ),

  /**
   * Единый эндпоинт действий (ADR 0004). Код действия разбирается здесь, на
   * «бэкенде»; фронт о его смысле не знает и знать не должен.
   */
  http.post(
    url('/predictions/:id/actions/:code'),
    guarded(async (user, { params, request }) => {
      const code = String(params['code'])
      if (!user.permissions.includes(code)) {
        return fail(403, `роль «${user.roleLabel}» не выполняет действие ${code}`)
      }

      const record = db().predictionById.get(String(params['id']))
      if (!record || !seesPrediction(user, record)) return fail(404, 'Прогноз не найден')

      const body = (await request.json().catch(() => ({}))) as Record<string, unknown>

      if (code === 'take') {
        record.status = 'IN_REVIEW'
        record.assignee = user.fullName
      } else if (code === 'release') {
        record.status = 'NEW'
        record.assignee = undefined
      } else if (code === 'decide') {
        // Выезд назначает итоговый уровень, а не согласие диспетчера. Четыре
        // случая таблицы ADR 0006 сходятся в два действия над заявкой.
        const level = String(body['dispatcherLevel'] ?? record.level)
        record.verdict = level === record.level ? 'AGREED' : 'CORRECTED'
        record.dispatcherLevel = level
        record.decidedAt = new Date().toISOString()
        record.assignee = record.assignee ?? user.fullName

        const needsOrder = level === 'HIGH' || level === 'CRITICAL'
        record.status = needsOrder ? 'ORDER_OPEN' : 'DECIDED'

        const order = record.orderId ? db().orderById.get(record.orderId) : undefined
        if (order && (order.status === 'AUTO_CREATED' || order.status === 'CONFIRMED')) {
          // Решение по прогнозу и подтверждает заявку, и отклоняет её.
          order.status = needsOrder ? 'CONFIRMED' : 'REJECTED'
          order.actions = orderActions(order.status, order.causesRef)
        }
      }

      console.info('[mocks] действие над прогнозом', { id: record.id, code, body })
      return ok(detailFor(record, user))
    }),
  ),

  /* карта */
  http.get(
    url('/facilities/lines'),
    guarded(async (user) =>
      ok({
        type: 'FeatureCollection',
        features: COLLECTORS.filter((c) =>
          visibleTo(user, c.district, collectorLabel(c.code)),
        ).map((c) => ({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: c.line },
          properties: { collector: c.label },
        })),
      }),
    ),
  ),

  http.get(
    url('/facilities'),
    guarded(async (user, { request }) => {
      if (devFlags.get('failLists')) return fail(500, 'Дев-панель: списки намеренно падают')

      const direction = listParam(request, 'direction')
      const level = listParam(request, 'level')
      const district = param(request, 'district')
      const box = parseBbox(param(request, 'bbox'))

      // На карте показываем по одной точке на объект — с самым тяжёлым прогнозом.
      const worst = new Map<string, PredictionRecord>()
      for (const record of db().predictions) {
        if (!seesPrediction(user, record)) continue
        if (direction.length && !direction.includes(record.direction)) continue
        if (level.length && !level.includes(record.level)) continue
        if (district && record.facility.district !== district) continue
        if (box && !insideBbox(box, record.facility.lon, record.facility.lat)) continue
        const current = worst.get(record.facility.id)
        if (!current || current.probability < record.probability) {
          worst.set(record.facility.id, record)
        }
      }

      const features: FacilityFeature[] = [...worst.values()].map((record) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [record.facility.lon, record.facility.lat] },
        properties: {
          facilityId: record.facility.id,
          predictionId: record.id,
          direction: record.direction,
          level: record.level,
          probability: record.probability,
          address: record.facility.address,
          collector: record.facility.collector,
        },
      }))

      return ok({ type: 'FeatureCollection', features })
    }),
  ),

  http.get(
    url('/facilities/:id'),
    guarded(async (user, { params }) => {
      const id = String(params['id'])
      const record = db().predictions.find((p) => p.facility.id === id && seesPrediction(user, p))
      if (!record) return fail(404, 'Объект не найден')
      return ok(record.facility)
    }),
  ),

  /* заявки */
  http.get(
    url('/orders'),
    guarded(async (user, { request }) => {
      if (devFlags.get('failLists')) return fail(500, 'Дев-панель: списки намеренно падают')

      const status = listParam(request, 'status')
      const dueBefore = param(request, 'dueBefore')

      const filtered = db().orders.filter((o) => {
        if (!seesOrder(user, o)) return false
        if (status.length && !status.includes(o.status)) return false
        if (dueBefore && o.dueAt > `${dueBefore}T23:59:59.999Z`) return false
        return true
      })

      const sorted = applySort(filtered, param(request, 'sort'), orderSortValue)
      const page = paginate(sorted, request)
      return ok({ ...page, items: page.items.map((order) => orderFor(order, user)) })
    }),
  ),

  http.get(
    url('/orders/:id'),
    guarded(async (user, { params }) => {
      const order = db().orderById.get(String(params['id']))
      if (!order || !seesOrder(user, order)) return fail(404, 'Заявка не найдена')
      return ok(orderFor(order, user))
    }),
  ),

  http.post(
    url('/orders/:id/actions/:code'),
    guarded(async (user, { params, request }) => {
      const code = String(params['code'])
      if (!user.permissions.includes(code)) {
        return fail(403, `роль «${user.roleLabel}» не выполняет действие ${code}`)
      }

      const order = db().orderById.get(String(params['id']))
      if (!order || !seesOrder(user, order)) return fail(404, 'Заявка не найдена')

      const body = (await request.json().catch(() => ({}))) as Record<string, unknown>

      if (code === 'assign') {
        // Бригада и срок называются одним действием. Раньше «исполнителя»
        // спрашивали при подтверждении, а «бригаду» при начале работ.
        order.status = 'IN_PROGRESS'
        order.dueAt = String(body['dueAt'] ?? order.dueAt)
      } else if (code === 'reject') {
        order.status = 'REJECTED'
      } else if (code === 'close') {
        // Исход бригады выбирает терминальный статус и закрывает прогноз тем же
        // исходом. ADR 0006: два поля с одним смыслом разошлись бы на первой
        // же правке, поэтому у прогноза своего поля исхода нет.
        const confirmed = body['factConfirmed'] === true
        order.status = confirmed ? 'CLOSED_CONFIRMED' : 'CLOSED_NOT_CONFIRMED'
        order.outcome = {
          actualCause: String(body['actualCause'] ?? ''),
          // Принимаем только boolean — форма шлёт именно его, а заглушка не
          // должна быть терпимее сервера в поле, от которого зависит обучение.
          factConfirmed: confirmed,
          comment: String(body['comment'] ?? ''),
          closedAt: new Date().toISOString(),
        }
        const prediction = db().predictionById.get(order.predictionId)
        if (prediction) {
          prediction.status = order.status
        }
      }

      order.actions = orderActions(order.status, order.causesRef)

      console.info('[mocks] действие над заявкой', { id: order.id, code, body })
      return ok(orderFor(order, user))
    }),
  ),

  /* метрики и дашборд */

  /**
   * Качество модели от роли не зависит: оно измерено на всей сети. Резать его
   * границей видимости значило бы показывать четыре разных Precision.
   */
  http.get(
    url('/metrics/models'),
    guarded(async () => ok(db().metrics)),
  ),

  http.get(
    url('/metrics/pipeline'),
    guarded(async () => ok(db().pipeline)),
  ),

  http.get(
    url('/dashboard/summary'),
    guarded(async (user) => {
      const { predictions, orders } = db()
      const visible = predictions.filter((p) => seesPrediction(user, p))
      const summary: DashboardSummary = {
        byLevel: {},
        byDirection: {},
        byStatus: {},
        byOrderStatus: {},
        total: visible.length,
      }
      for (const p of visible) {
        summary.byLevel[p.level] = (summary.byLevel[p.level] ?? 0) + 1
        summary.byDirection[p.direction] = (summary.byDirection[p.direction] ?? 0) + 1
        summary.byStatus[p.status] = (summary.byStatus[p.status] ?? 0) + 1
      }
      for (const order of orders.filter((o) => seesOrder(user, o))) {
        summary.byOrderStatus[order.status] = (summary.byOrderStatus[order.status] ?? 0) + 1
      }
      return ok(summary)
    }),
  ),

  http.get(
    url('/dashboard/top-risks'),
    guarded(async (user, { request }) => {
      const limit = Number(param(request, 'limit') ?? '10') || 10
      const top = db()
        .predictions.filter(
          (p) => seesPrediction(user, p) && p.status !== 'REJECTED' && p.status !== 'CLOSED',
        )
        .sort((a, b) => b.probability - a.probability)
        .slice(0, limit)
        .map(toPrediction)
      return ok(top)
    }),
  ),
]

/** Сброс состояния между тестами и по кнопке дев-панели. */
export function resetMockDb(): void {
  resetDb()
  resetUsers()
}
