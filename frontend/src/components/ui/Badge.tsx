import type { HTMLAttributes } from 'react'
import { clsx } from 'clsx'

type BadgeVariant = 'success' | 'danger' | 'warning' | 'neutral' | 'info'

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant
}

const variantClasses: Record<BadgeVariant, string> = {
  success: 'bg-success/20 text-success',
  danger: 'bg-danger/20 text-danger',
  warning: 'bg-warning/20 text-warning',
  neutral: 'bg-surface2 text-muted',
  info: 'bg-primary/20 text-primary',
}

export function Badge({ variant = 'neutral', className, ...props }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium',
        variantClasses[variant],
        className
      )}
      {...props}
    />
  )
}
