/**
 * Одноразовый код по времени, RFC 6238. Зеркало `backend/app/auth/totp.py`.
 *
 * Заглушка считает код по-настоящему, а не принимает любые шесть цифр.
 * Иначе второй фактор на стенде оставался бы декорацией: жюри сканировало бы
 * QR, а подошла бы и строка `000000`. Секрет заглушка сама и выдала, значит
 * проверить код она может.
 *
 * Считает Web Crypto: HMAC-SHA1 есть в браузере и в Node. Своя реализация
 * SHA-1 добавила бы шестьдесят строк ради того, что уже стоит в платформе.
 */

export const DIGITS = 6
export const STEP_SECONDS = 30

/** Соседние шаги, которые тоже принимаются. Закрывает расхождение часов. */
export const WINDOW_STEPS = 1

/** Разбирает base32 без заполняющих знаков: телефоны их не ставят. */
export function decodeBase32(secret: string): Uint8Array {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  const clean = secret.replace(/[\s=]/g, '').toUpperCase()
  const bytes: number[] = []
  let buffer = 0
  let bits = 0

  for (const symbol of clean) {
    const index = alphabet.indexOf(symbol)
    if (index < 0) throw new Error(`не base32: ${symbol}`)
    buffer = (buffer << 5) | index
    bits += 5
    if (bits >= 8) {
      bits -= 8
      bytes.push((buffer >> bits) & 0xff)
    }
  }
  return new Uint8Array(bytes)
}

/** Восьмибайтовый счётчик шагов, старший байт первым. */
function counterBytes(counter: number): Uint8Array {
  const bytes = new Uint8Array(8)
  let rest = counter
  for (let i = 7; i >= 0; i -= 1) {
    bytes[i] = rest & 0xff
    rest = Math.floor(rest / 256)
  }
  return bytes
}

async function codeForCounter(key: Uint8Array, counter: number): Promise<string> {
  const material = await crypto.subtle.importKey(
    'raw',
    key as unknown as BufferSource,
    { name: 'HMAC', hash: 'SHA-1' },
    false,
    ['sign'],
  )
  const signature = new Uint8Array(
    await crypto.subtle.sign('HMAC', material, counterBytes(counter) as unknown as BufferSource),
  )

  // Динамическое усечение RFC 4226: младшие четыре бита последнего байта
  // указывают, откуда взять четыре байта результата.
  const offset = (signature[signature.length - 1] as number) & 0x0f
  const number =
    (((signature[offset] as number) & 0x7f) << 24) |
    ((signature[offset + 1] as number) << 16) |
    ((signature[offset + 2] as number) << 8) |
    (signature[offset + 3] as number)

  return String(number % 10 ** DIGITS).padStart(DIGITS, '0')
}

/** Код на указанный момент. Без аргумента — на сейчас. */
export async function codeAt(secret: string, moment = Date.now()): Promise<string> {
  const counter = Math.floor(moment / 1000 / STEP_SECONDS)
  return codeForCounter(decodeBase32(secret), counter)
}

/**
 * Сверяет код с секретом. Битый секрет или не цифры дают отказ.
 *
 * Принимается и соседний шаг: часы телефона расходятся с часами стенда.
 */
export async function verify(secret: string, code: string, moment = Date.now()): Promise<boolean> {
  const cleaned = code.replace(/\s/g, '')
  if (cleaned.length !== DIGITS || !/^\d+$/.test(cleaned)) return false

  let key: Uint8Array
  try {
    key = decodeBase32(secret)
  } catch {
    return false
  }

  const now = Math.floor(moment / 1000 / STEP_SECONDS)
  for (let shift = -WINDOW_STEPS; shift <= WINDOW_STEPS; shift += 1) {
    if ((await codeForCounter(key, now + shift)) === cleaned) return true
  }
  return false
}
