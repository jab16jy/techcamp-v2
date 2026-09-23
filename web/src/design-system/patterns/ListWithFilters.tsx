import type { ReactNode } from 'react'
import { cn } from '../ui/utils'

export interface ListFilter {
  key: string
  label: string
}

export interface ListWithFiltersProps<T> {
  filters: ListFilter[]
  activeFilter: string
  onFilterChange: (key: string) => void
  items: T[]
  getKey: (item: T) => string
  renderItem: (item: T) => ReactNode
  emptyState?: ReactNode
  className?: string
}

/** Inset grouped list (iOS structure) with a filter row above it. */
export function ListWithFilters<T>({
  filters,
  activeFilter,
  onFilterChange,
  items,
  getKey,
  renderItem,
  emptyState,
  className,
}: ListWithFiltersProps<T>) {
  return (
    <div className={className}>
      <div role="tablist" aria-label="Filtros" className="flex gap-2 overflow-x-auto px-4 py-2">
        {filters.map((filter) => {
          const isActive = filter.key === activeFilter
          return (
            <button
              key={filter.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => onFilterChange(filter.key)}
              className={cn(
                'min-h-12 shrink-0 rounded-full border px-4 text-sm font-medium',
                isActive
                  ? 'border-brand bg-brand text-surface-raised'
                  : 'border-text/20 bg-surface-raised text-text',
              )}
            >
              {filter.label}
            </button>
          )
        })}
      </div>
      {items.length === 0 ? (
        emptyState
      ) : (
        <ul className="mx-4 divide-y divide-text/10 rounded-lg bg-surface-raised">
          {items.map((item) => (
            <li key={getKey(item)}>{renderItem(item)}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
