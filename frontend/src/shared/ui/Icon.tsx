/**
 * Штриховые иконки 16 px, толщина 1.5. Инлайном, без спрайта и без библиотеки:
 * их девять штук, и так они не зависят от загрузки внешнего файла.
 *
 * Иконка без подписи допустима только в рельсе, где есть title (docs/05-ui-kit.md).
 */
import type { SVGProps } from 'react'

const paths = {
  dashboard: 'M3 3h6v7H3zM3 12h6v3H3zM11 3h4v4h-4zM11 9h4v6h-4z',
  map: 'M2 4.5 6.5 3 11.5 5 16 3.5v10L11.5 15 6.5 13 2 14.5zM6.5 3v10M11.5 5v10',
  journal: 'M3 3h12v12H3zM3 6.5h12M6.5 3v12',
  orders: 'M4 3h10v12H4zM6.5 6.5h5M6.5 9h5M6.5 11.5h3',
  close: 'M4 4l10 10M14 4L4 14',
  chevronDown: 'M4 6.5l5 5 5-5',
  chevronLeft: 'M11 3.5l-5 5 5 5',
  chevronRight: 'M7 3.5l5 5-5 5',
  download: 'M9 2.5v9M5 8l4 4 4-4M3 15h12',
  external: 'M7 3H3v12h12v-4M10 3h5v5M15 3l-7 7',
  filter: 'M2.5 4h13l-5 5.5V15l-3-1.5V9.5z',
  warning: 'M9 2.5 16.5 15h-15zM9 7v4M9 13.2v.1',
  refresh: 'M15 9a6 6 0 1 1-1.8-4.3M15 3v3h-3',
  check: 'M3.5 9.5l3.5 3.5 7.5-8',
} as const

export type IconName = keyof typeof paths

export interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName
  size?: number
}

export function Icon({ name, size = 16, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 18 18"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d={paths[name]} />
    </svg>
  )
}
