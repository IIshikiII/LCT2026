/**
 * Учётные записи заглушки. Зеркало таблицы `app_user` и наборов тестового
 * стенда.
 *
 * Это стенд-ин каталога, а не тестовая фикстура (ADR 0007): те же четыре роли,
 * те же границы видимости, тот же двухшаговый вход и тот же настоящий TOTP.
 * Любые шесть цифр здесь **не подходят**: секрет заглушка выдала сама, значит
 * и проверить код она может.
 *
 * Набор — это четыре учётные записи, по одной на роль. Первый набор идёт без
 * суффикса, следующие получают номер: `ods-2`, `district-2` и так далее. На
 * стенде это даёт каждому проверяющему свой комплект, чтобы пройти второй
 * фактор, не отнимая ключ у соседа.
 */
import { COLLECTORS, DISTRICTS, collectorLabel } from './catalog'

/** Пароль всех демонстрационных записей. Демо, скрывать нечего. */
export const DEMO_PASSWORD = 'collector'

/** Длина одноразового кода. Та же, что у сервера. */
export const CODE_LENGTH = 6

export interface MockUser {
  username: string
  fullName: string
  role: string
  roleLabel: string
  /** `ALL`, `DISTRICT` или `COMPLEX`. */
  scopeKind: string
  scopeValue?: string
  permissions: string[]
  /** Номер набора. Записи одного набора создаются и удаляются вместе. */
  demoSet: number
  /** Заведённый ключ второго фактора. Пусто — ключа ещё нет. */
  secret?: string
  /** Ключ, выданный на первом шаге входа и ещё не подтверждённый кодом. */
  pendingSecret?: string
}

const DISPATCH = ['take', 'release', 'decide', 'assign', 'reject']

/** Первый комплекс и первый район посева. Границы узких ролей. */
const FIRST_COMPLEX = collectorLabel(COLLECTORS[0]?.code ?? '')
const FIRST_DISTRICT = DISTRICTS[0]?.code ?? ''

interface RoleSeed {
  base: string
  fullName: string
  role: string
  roleLabel: string
  scopeKind: string
  scopeValue?: string
  permissions: string[]
}

const ROLE_SEEDS: RoleSeed[] = [
  {
    base: 'ods',
    fullName: 'Иванов И. И.',
    role: 'ODS_DISPATCHER',
    roleLabel: 'Диспетчер ОДС',
    scopeKind: 'ALL',
    permissions: DISPATCH,
  },
  {
    base: 'district',
    fullName: 'Петров П. П.',
    role: 'DISTRICT_DISPATCHER',
    roleLabel: 'Диспетчер района',
    scopeKind: 'DISTRICT',
    scopeValue: FIRST_DISTRICT,
    permissions: DISPATCH,
  },
  {
    base: 'tech',
    fullName: 'Сидоров С. С.',
    role: 'TECHNICIAN',
    roleLabel: 'Техник',
    scopeKind: 'COMPLEX',
    scopeValue: FIRST_COMPLEX,
    permissions: [],
  },
  {
    base: 'crew',
    fullName: 'Бригада 1',
    role: 'RESPONSE_TEAM',
    roleLabel: 'Группа реагирования',
    scopeKind: 'ALL',
    permissions: ['close'],
  },
]

/** Логин набора. Первый набор идёт без суффикса: он же путь из README. */
export function usernameFor(base: string, set: number): string {
  return set === 1 ? base : `${base}-${set}`
}

function buildSet(set: number): MockUser[] {
  return ROLE_SEEDS.map((seed) => ({
    username: usernameFor(seed.base, set),
    fullName: set === 1 ? seed.fullName : `${seed.fullName} (${set})`,
    role: seed.role,
    roleLabel: seed.roleLabel,
    scopeKind: seed.scopeKind,
    scopeValue: seed.scopeValue,
    permissions: seed.permissions,
    demoSet: set,
  }))
}

export const MOCK_USERS: MockUser[] = buildSet(1)

/** Возвращает учётные записи в исходное состояние: один набор без ключей. */
export function resetUsers(): void {
  MOCK_USERS.length = 0
  MOCK_USERS.push(...buildSet(1))
}

export function userByName(username: string): MockUser | undefined {
  return MOCK_USERS.find((user) => user.username === username.trim())
}

/** Номера наборов по возрастанию. */
export function setNumbers(): number[] {
  return [...new Set(MOCK_USERS.map((user) => user.demoSet))].sort((a, b) => a - b)
}

/** Заводит следующий набор и отдаёт его номер. */
export function createSet(): number {
  const next = Math.max(0, ...setNumbers()) + 1
  MOCK_USERS.push(...buildSet(next))
  return next
}

/** Удаляет набор целиком. Отдаёт признак того, что набор был. */
export function deleteSet(set: number): boolean {
  const before = MOCK_USERS.length
  const keep = MOCK_USERS.filter((user) => user.demoSet !== set)
  MOCK_USERS.length = 0
  MOCK_USERS.push(...keep)
  return MOCK_USERS.length < before
}

/** Новый секрет второго фактора: 160 бит в base32, как у сервера. */
export function generateSecret(): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  const bytes = crypto.getRandomValues(new Uint8Array(20))
  let bits = 0
  let buffer = 0
  let out = ''
  for (const byte of bytes) {
    buffer = (buffer << 8) | byte
    bits += 8
    while (bits >= 5) {
      bits -= 5
      out += alphabet[(buffer >> bits) & 31]
    }
  }
  if (bits > 0) out += alphabet[(buffer << (5 - bits)) & 31]
  return out
}

/**
 * Видит ли роль этот объект.
 *
 * Границы те же, что у сервера: район сравнивается с кодом округа, комплекс —
 * с названием коллектора. Настоящих кодов комплексов в заглушке нет, и подпись
 * коллектора играет их роль.
 */
export function visibleTo(user: MockUser, district: string, collector: string): boolean {
  if (user.scopeKind === 'DISTRICT') return district === user.scopeValue
  if (user.scopeKind === 'COMPLEX') return collector === user.scopeValue
  return true
}
