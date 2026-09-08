/**
 * Граница ошибок. Обязательна вокруг каждого блока карточки (ADR 0003):
 * данные блока приходят с бэкенда, их форму фронт не контролирует, и сломанный
 * блок не должен уносить карточку целиком.
 *
 * Классовый компонент — потому что хуковой альтернативы componentDidCatch в
 * React по-прежнему нет.
 *
 * Сброса состояния внутри нет намеренно: чтобы граница «забыла» об ошибке,
 * вызывающий передаёт `key` (обычно id сущности). Это дешевле и предсказуемее,
 * чем setState в componentDidUpdate.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'

export interface ErrorBoundaryProps {
  children: ReactNode
  /** Что показать вместо упавшего поддерева. */
  fallback: ReactNode
}

interface ErrorBoundaryState {
  failed: boolean
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ui] поддерево упало и заменено запасным содержимым', error, info.componentStack)
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}
