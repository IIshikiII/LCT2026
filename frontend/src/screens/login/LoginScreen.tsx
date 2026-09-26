/**
 * Вход в систему. Два шага и одна развилка.
 *
 * Периметр приложения — четыре экрана (spec §1), и этот в них не входит: он не
 * показывает данных и не живёт в рельсе. Это ворота перед периметром, поэтому
 * и лежит отдельно, и в `router.tsx` его нет.
 *
 * Справа от формы может появиться панель тестового стенда. Она рисуется только
 * тогда, когда сервер поднят с флагом `TEST_STAND`, и в промышленной установке
 * её не бывает.
 *
 * Шаг первый — логин и пароль. Шаг второй — код из аутентификатора. Развилка
 * между ними: у записи без заведённого ключа сервер отдаёт секрет, и тот же
 * второй шаг подтверждает его.
 *
 * Ключ заводится сканированием QR. Ручной ввод и ссылка `otpauth://` остались
 * запасными путями и свёрнуты: камера берёт код не с каждого экрана, а ключ
 * читается всегда.
 *
 * Поле статуса из ответа сервера — строка, и ветвления по её значениям здесь
 * нет: экран смотрит только на то, пришёл секрет или нет.
 */
import { useState } from 'react'
import type { FormEvent } from 'react'
import { useConfirmCode, useLogin } from '@/shared/api/queries'
import type { LoginChallenge } from '@/shared/api/types'
import { useAuth } from '@/shared/auth/context'
import { Button } from '@/shared/ui/Button'
import { Field, TextInput } from '@/shared/ui/Field'
import { QrCode } from '@/shared/ui/QrCode'
import { TestStandPanel } from './TestStandPanel'

/** Длина одноразового кода. Та же, что у сервера и у аутентификаторов. */
const CODE_LENGTH = 6

/**
 * Приложения, которые заведомо читают наш ключ.
 *
 * Список не исчерпывающий и не является требованием: формат стандартный
 * (RFC 6238, HMAC-SHA1, шаг 30 секунд, шесть цифр), и подходит любой
 * аутентификатор. Имена нужны тем, у кого ни одного ещё не стоит.
 *
 * В списке есть и настольные приложения: ключ живёт в приложении, а не в
 * телефоне, и заводить его можно там, где удобно.
 */
const AUTHENTICATORS = [
  'Яндекс Ключ',
  'Google Authenticator',
  'Microsoft Authenticator',
  '1Password',
  'Bitwarden',
  'Aegis',
  'KeePassXC',
]

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

export function LoginScreen() {
  const { signIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [challenge, setChallenge] = useState<LoginChallenge | null>(null)

  const login = useLogin()
  const confirm = useConfirmCode()

  function submitPassword(event: FormEvent) {
    event.preventDefault()
    login.mutate(
      { username: username.trim(), password },
      {
        onSuccess: (next) => {
          setChallenge(next)
          setPassword('')
        },
      },
    )
  }

  function submitCode(event: FormEvent) {
    event.preventDefault()
    if (!challenge) return
    confirm.mutate(
      { mfaToken: challenge.mfaToken, code: code.trim() },
      {
        onSuccess: (session) =>
          signIn({ token: session.accessToken, user: session.user }),
      },
    )
  }

  function startOver() {
    setChallenge(null)
    setCode('')
    confirm.reset()
    login.reset()
  }

  return (
    <div className="flex h-full items-center justify-center gap-4 overflow-auto bg-bg p-4">
      <div className="w-full max-w-sm rounded border border-line bg-panel p-5">
        <h1 className="text-[15px] font-medium text-text">АРМ диспетчера ОДС</h1>
        <p className="mt-1 text-[12px] text-text-mute">
          прогнозирование аварий инженерных коллекторов
        </p>

        {challenge === null ? (
          <form onSubmit={submitPassword} className="mt-5 flex flex-col gap-3">
            <Field id="username" label="Логин" required>
              <TextInput
                id="username"
                name="username"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </Field>

            <Field id="password" label="Пароль" required>
              <TextInput
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>

            {login.isError ? (
              <p role="alert" className="text-[12px] text-risk-high">
                {errorText(login.error, 'Войти не удалось')}
              </p>
            ) : null}

            <Button type="submit" disabled={login.isPending || !username || !password}>
              {login.isPending ? 'Проверяем…' : 'Войти'}
            </Button>
          </form>
        ) : (
          <form onSubmit={submitCode} className="mt-5 flex flex-col gap-3">
            {challenge.secret ? <EnrollKey challenge={challenge} /> : null}

            <Field
              id="code"
              label="Код из приложения"
              required
              help={`${CODE_LENGTH} цифр, код меняется каждые 30 секунд`}
            >
              <TextInput
                id="code"
                name="code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={CODE_LENGTH}
                autoFocus
                className="mono tracking-[0.3em]"
                value={code}
                onChange={(event) => setCode(event.target.value)}
              />
            </Field>

            {confirm.isError ? (
              <p role="alert" className="text-[12px] text-risk-high">
                {errorText(confirm.error, 'Код не подошёл')}
              </p>
            ) : null}

            <Button type="submit" disabled={confirm.isPending || code.trim().length < CODE_LENGTH}>
              {confirm.isPending ? 'Проверяем…' : 'Подтвердить'}
            </Button>
            <Button type="button" kind="ghost" size="sm" onClick={startOver}>
              Назад к паролю
            </Button>
          </form>
        )}
      </div>

      <TestStandPanel />
    </div>
  )
}

/**
 * Регистрация ключа. Секрет приходит один раз и в базу попадает только после
 * верного кода, поэтому уйти с этого экрана, не заведя ключ, безопасно.
 *
 * Три пути завести ключ, по убыванию удобства: навести камеру на QR, открыть
 * ссылку с самого телефона, ввести ключ руками. Последний остаётся запасным:
 * камера не всегда берёт экран, а ключ читается всегда.
 */
function EnrollKey({ challenge }: { challenge: LoginChallenge }) {
  return (
    <div className="flex flex-col gap-3 rounded border border-line bg-sunken p-3">
      <p className="text-[12px] text-text-dim">
        Ключ второго фактора ещё не заведён. Наведите на код камеру
        аутентификатора и подтвердите кодом из него.
      </p>
      <p className="text-[11px] text-text-mute">
        Подойдёт любое: {AUTHENTICATORS.join(', ')}.
      </p>

      {challenge.otpauthUrl ? (
        <div className="flex justify-center">
          <QrCode
            value={challenge.otpauthUrl}
            size={200}
            label="QR-код для приложения-аутентификатора"
          />
        </div>
      ) : null}

      <details>
        <summary className="cursor-pointer list-none text-[12px] text-text-dim hover:text-text">
          Камера не берёт код
        </summary>
        <div className="mt-2 flex flex-col gap-2">
          <div>
            <span className="text-[11px] text-text-mute">Ключ для ручного ввода</span>
            <p className="mono break-all text-[12px] text-text select-all">{challenge.secret}</p>
          </div>
          {challenge.otpauthUrl ? (
            <a
              href={challenge.otpauthUrl}
              className="text-[12px] text-text-dim underline underline-offset-2 hover:text-text"
            >
              Открыть в приложении на телефоне
            </a>
          ) : null}
        </div>
      </details>
    </div>
  )
}
