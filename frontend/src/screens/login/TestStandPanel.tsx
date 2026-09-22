/**
 * Панель тестового стенда рядом с формой входа.
 *
 * Показывается, только когда сервер ответил `enabled: true`, то есть при флаге
 * `TEST_STAND`. В промышленной установке флага нет, панели нет, и кода этого
 * на экране не бывает.
 *
 * Зачем она. Ключ второго фактора остаётся в том приложении-аутентификаторе,
 * где его завели, и второму человеку он недоступен. Один набор учёток на всех
 * означает, что первый же проверяющий заведёт ключ у себя, а остальные
 * останутся снаружи. Кнопка внизу выдаёт следующему свой набор из четырёх
 * ролей.
 *
 * Отметка «2FA пройдена» рядом с логином отвечает на единственный вопрос,
 * который у проверяющего возникает: этот набор свободен или уже занят.
 */
import { useCreateTestSet, useDeleteTestSet, useTestStand } from '@/shared/api/queries'
import type { TestAccountSet } from '@/shared/api/types'
import { Button } from '@/shared/ui/Button'
import { Icon } from '@/shared/ui/Icon'

/**
 * Высота ограничена намеренно. Без неё при десятке наборов прокручивалась бы
 * вся страница вместе с формой входа, а прокручиваться должен список.
 */
const PANEL = 'flex max-h-[80vh] w-80 shrink-0 flex-col rounded border border-line bg-panel'

export function TestStandPanel() {
  const stand = useTestStand()
  const create = useCreateTestSet()
  const remove = useDeleteTestSet()

  // Выключенный стенд, недоступный сервер, битый ответ — панели просто нет.
  if (!stand.data?.enabled) return null

  const { password, sets } = stand.data

  return (
    <aside
      aria-label="Тестовые учётные записи"
      className={PANEL}
    >
      <div className="border-b border-line px-3 py-2">
        <div className="flex items-center gap-1.5">
          <Icon name="warning" size={13} />
          <span className="text-[13px] font-medium text-text">Тестовый стенд</span>
        </div>
        <p className="mt-1 text-[11px] text-text-mute">
          Набор — это четыре роли. Возьмите свободный или создайте новый: ключ второго
          фактора остаётся в том приложении, где его завели.
        </p>
        <p className="mt-1 text-[11px] text-text-mute">
          Пароль у всех: <span className="mono text-text-dim select-all">{password}</span>
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {sets.length === 0 ? (
          <p className="px-3 py-4 text-[12px] text-text-mute">
            Наборов нет. Создайте первый кнопкой ниже.
          </p>
        ) : (
          sets.map((group) => (
            <SetBlock
              key={group.set}
              group={group}
              onDelete={() => remove.mutate(group.set)}
              busy={remove.isPending}
            />
          ))
        )}
      </div>

      <div className="border-t border-line p-2">
        <Button
          size="sm"
          className="w-full"
          disabled={create.isPending}
          onClick={() => create.mutate()}
        >
          {create.isPending ? 'Создаём…' : 'Создать ещё один набор учёток'}
        </Button>
      </div>
    </aside>
  )
}

function SetBlock({
  group,
  onDelete,
  busy,
}: {
  group: TestAccountSet
  onDelete: () => void
  busy: boolean
}) {
  const free = group.accounts.filter((item) => !item.mfaEnrolled).length

  return (
    <section className="border-b border-line px-3 py-2 last:border-b-0">
      {/* Не `header`: он получает роль `banner`, и тест перестаёт отличать
          панель от шапки приложения. */}
      <div className="flex items-center gap-2">
        <span className="text-[12px] font-medium text-text">Набор {group.set}</span>
        <span className="text-[11px] text-text-mute">
          {free === group.accounts.length
            ? 'свободен'
            : free === 0
              ? 'занят целиком'
              : `свободно ролей: ${free}`}
        </span>
        <Button
          size="sm"
          kind="ghost"
          className="ml-auto"
          disabled={busy}
          onClick={onDelete}
          aria-label={`Удалить набор ${group.set}`}
        >
          Удалить
        </Button>
      </div>

      <ul className="mt-1 flex flex-col gap-1">
        {group.accounts.map((account) => (
          <li key={account.username} className="flex items-baseline gap-2 text-[12px]">
            <span className="mono shrink-0 text-text select-all">{account.username}</span>
            <span className="truncate text-text-mute">{account.roleLabel}</span>
            <span
              className={`ml-auto shrink-0 text-[11px] ${
                account.mfaEnrolled ? 'text-text-mute' : 'text-risk-low'
              }`}
            >
              {account.mfaEnrolled ? '2FA пройдена' : '2FA не пройдена'}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
