import { WarningOctagon } from '@phosphor-icons/react'
import { Component, type ErrorInfo, type ReactNode } from 'react'

import { Button } from '../ui/button'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Last line of defence for render-time exceptions: shows a recoverable error
 * screen instead of a blank page (Section 4 — exception handling).
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled UI error', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div
        role="alert"
        className="flex min-h-screen flex-col items-center justify-center gap-4 bg-bg p-6 text-center text-text"
      >
        <WarningOctagon size={40} className="text-danger" weight="duotone" />
        <h1 className="text-lg font-semibold">Something went wrong</h1>
        <p className="max-w-md text-small text-muted">{this.state.error.message}</p>
        <Button onClick={() => window.location.reload()}>Reload the app</Button>
      </div>
    )
  }
}
