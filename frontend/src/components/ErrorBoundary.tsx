import { Component } from 'react'
import type { ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * PHASE5_STEP10 — wraps the protected layout: a render crash in one page shows
 * a recoverable error screen instead of unmounting the whole app (which made
 * every nav item appear dead until a reload).
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error('[UI] Uncaught render error:', error)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-background text-text flex items-center justify-center p-6">
          <div className="max-w-md text-center space-y-3">
            <h1 className="text-xl font-bold">Something went wrong</h1>
            <p className="text-sm text-muted font-mono break-words">{this.state.error.message}</p>
            <button
              className="px-4 py-2 rounded bg-primary text-white hover:bg-primary/80"
              onClick={() => window.location.reload()}
            >
              Refresh Page
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
