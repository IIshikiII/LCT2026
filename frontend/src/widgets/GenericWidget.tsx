/**
 * Запасной виджет для кода, которого нет в реестре.
 *
 * Дашборд не падает и честно сообщает, чего не хватает: код виджета виден, его
 * можно отнести бэкендеру или завести под него компонент.
 */
import { Panel } from '@/shared/ui/Panel'

export function GenericWidget({ code }: { code: string }) {
  return (
    <Panel title="Виджет не реализован" className="col-span-2">
      <p className="text-[13px] text-text-mute">
        Дашборд запросил виджет <code className="mono text-text-dim">{code}</code>, но в реестре
        такого нет. Остальные виджеты работают.
      </p>
    </Panel>
  )
}
