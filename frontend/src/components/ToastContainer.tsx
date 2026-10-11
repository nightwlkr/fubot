import { useAlertStore } from '../store/alertStore'

const levelStyle: Record<string, string> = {
  INFO: 'border-primary text-text',
  SUCCESS: 'border-success text-success',
  WARNING: 'border-warning text-warning',
  ERROR: 'border-danger text-danger',
  CRITICAL: 'border-danger text-danger',
}

export default function ToastContainer() {
  const { toasts, dismiss } = useAlertStore()
  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map(t => (
        <div
          key={t.id}
          className={`bg-surface border-l-4 ${levelStyle[t.level] ?? levelStyle.INFO} rounded shadow-lg px-4 py-3 text-sm flex items-start gap-3`}
        >
          <span className="flex-1 break-words">{t.message}</span>
          <button
            className="text-muted hover:text-text"
            onClick={() => dismiss(t.id)}
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}
