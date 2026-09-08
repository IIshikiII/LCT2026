/**
 * Флаги экранов. Выключенный экран пропадает и из рельса, и из роутера.
 *
 * Зачем: если экран не успевает к защите, лучше четыре работающих пункта меню,
 * чем пять, один из которых белый. Переключается правкой одной строки.
 *
 * Периметр — ровно эти четыре экрана (spec §1). Не расширять, пока они не
 * закончены.
 */
export const features = {
  dashboard: true,
  map: true,
  journal: true,
  orders: true,
} as const

export type FeatureKey = keyof typeof features

export function isEnabled(key: FeatureKey): boolean {
  return features[key]
}
