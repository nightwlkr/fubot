import { forwardRef } from 'react'
import type { HTMLAttributes } from 'react'
import { clsx } from 'clsx'

const Table = forwardRef<HTMLTableElement, HTMLAttributes<HTMLTableElement>>(
  ({ className, ...props }, ref) => (
    <div className="w-full overflow-auto">
      <table ref={ref} className={clsx('w-full text-sm', className)} {...props} />
    </div>
  )
)
Table.displayName = 'Table'

const TableHead = forwardRef<HTMLTableSectionElement, HTMLAttributes<HTMLTableSectionElement>>(
  ({ className, ...props }, ref) => (
    <thead ref={ref} className={clsx('border-b border-border', className)} {...props} />
  )
)
TableHead.displayName = 'TableHead'

const TableRow = forwardRef<HTMLTableRowElement, HTMLAttributes<HTMLTableRowElement>>(
  ({ className, ...props }, ref) => (
    <tr ref={ref} className={clsx('border-b border-border/50 hover:bg-surface2/50 transition-colors', className)} {...props} />
  )
)
TableRow.displayName = 'TableRow'

const TableHeadCell = forwardRef<HTMLTableCellElement, HTMLAttributes<HTMLTableCellElement>>(
  ({ className, ...props }, ref) => (
    <th ref={ref} className={clsx('text-left text-xs font-medium text-muted uppercase tracking-wide py-3 px-4', className)} {...props} />
  )
)
TableHeadCell.displayName = 'TableHeadCell'

const TableCell = forwardRef<HTMLTableCellElement, HTMLAttributes<HTMLTableCellElement>>(
  ({ className, ...props }, ref) => (
    <td ref={ref} className={clsx('py-3 px-4 text-text', className)} {...props} />
  )
)
TableCell.displayName = 'TableCell'

export { Table, TableHead, TableRow, TableHeadCell, TableCell }
