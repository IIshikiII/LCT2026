/**
 * Список активных направлений.
 *
 * Всё, что нужно, чтобы добавить направление: положить рядом файл с
 * `DirectionPlugin` и дописать его сюда. Ни генератор данных, ни хендлеры, ни
 * тем более приложение об этом не узнают — они перебирают список.
 */
import { devFlags } from '../devFlags'
import { fireRisk } from './fireRisk'
import { floodRisk } from './floodRisk'
import { sensorFailure } from './sensorFailure'
import { unauthorizedAccess } from './unauthorizedAccess'
import type { DirectionPlugin } from './types'

/**
 * Направления, по которым есть данные. Износ инфраструктуры убран: признаков
 * для него выгрузка не даёт, а бриф разрешает взять подмножество направлений.
 */
export const BASE_DIRECTIONS: DirectionPlugin[] = [
  sensorFailure,
  fireRisk,
  unauthorizedAccess,
]

/** Ещё одно — только когда включён соответствующий флаг дев-панели. */
export function activeDirections(): DirectionPlugin[] {
  return devFlags.get('extraDirection') ? [...BASE_DIRECTIONS, floodRisk] : BASE_DIRECTIONS
}

export function directionByCode(code: string): DirectionPlugin | undefined {
  return activeDirections().find((d) => d.meta.code === code)
}

export { fireRisk, floodRisk, sensorFailure, unauthorizedAccess }
export type { DirectionPlugin, PredictionSeed } from './types'
