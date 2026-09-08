/**
 * Склейка классов. Отдельной зависимости (clsx) ради восьми строк не берём.
 */
export type ClassValue = string | false | null | undefined

export function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(' ')
}
