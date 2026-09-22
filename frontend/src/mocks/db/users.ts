/**
 * Учётные записи заглушки. По одной на каждую роль из ответа заказчика 4.1.
 *
 * Это стенд-ин каталога, а не тестовая фикстура (ADR 0007): те же четыре роли,
 * те же границы видимости и тот же двухшаговый вход, что у сервера.
 *
 * **Чего заглушка не делает.** Она не считает одноразовый код по алгоритму
 * TOTP. Общего секрета с настоящим аутентификатором у неё нет, и проверять
 * было бы нечего, поэтому подходит любые шесть цифр. Пароль проверяется:
 * иначе экран входа нельзя показать в работе, а он обязан отказывать.
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
  /** Заведён ли ключ второго фактора. У одной записи нет — ради демонстрации. */
  enrolled: boolean
}

const DISPATCH = ['take', 'release', 'decide', 'assign', 'reject']

/** Первый комплекс и первый район посева. Границы узких ролей. */
const FIRST_COMPLEX = collectorLabel(COLLECTORS[0]?.code ?? '')
const FIRST_DISTRICT = DISTRICTS[0]?.code ?? ''

export const MOCK_USERS: MockUser[] = [
  {
    username: 'ods',
    fullName: 'Иванов И. И.',
    role: 'ODS_DISPATCHER',
    roleLabel: 'Диспетчер ОДС',
    scopeKind: 'ALL',
    permissions: DISPATCH,
    enrolled: true,
  },
  {
    username: 'district',
    fullName: 'Петров П. П.',
    role: 'DISTRICT_DISPATCHER',
    roleLabel: 'Диспетчер района',
    scopeKind: 'DISTRICT',
    scopeValue: FIRST_DISTRICT,
    permissions: DISPATCH,
    enrolled: true,
  },
  {
    username: 'tech',
    fullName: 'Сидоров С. С.',
    role: 'TECHNICIAN',
    roleLabel: 'Техник',
    scopeKind: 'COMPLEX',
    scopeValue: FIRST_COMPLEX,
    permissions: [],
    enrolled: true,
  },
  {
    username: 'crew',
    fullName: 'Бригада 1',
    role: 'RESPONSE_TEAM',
    roleLabel: 'Группа реагирования',
    scopeKind: 'ALL',
    permissions: ['close'],
    // Ключ не заведён: первый вход этой роли проходит регистрацию целиком.
    enrolled: false,
  },
]

/**
 * Возвращает признак заведённого ключа в исходное состояние.
 *
 * Вход меняет запись: подтверждённый ключ остаётся заведённым. Между тестами
 * это протекало бы, и сценарий регистрации проходил бы только первым.
 */
export function resetUsers(): void {
  for (const user of MOCK_USERS) {
    user.enrolled = user.username !== 'crew'
  }
}

export function userByName(username: string): MockUser | undefined {
  return MOCK_USERS.find((user) => user.username === username.trim())
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
