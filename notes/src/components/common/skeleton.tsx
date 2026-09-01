import { cn } from '@/lib/utils/cn'

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn('animate-pulse rounded-md bg-line/70', className)}
    />
  )
}

/** Matches the real row's height so switching to data causes no layout shift. */
export function RowSkeleton() {
  return (
    <div className="flex items-center gap-3 px-3 py-2.5">
      <Skeleton className="size-[18px] rounded-full" />
      <div className="flex-1 space-y-1.5">
        <Skeleton className="h-3.5 w-[52%]" />
        <Skeleton className="h-2.5 w-[28%]" />
      </div>
    </div>
  )
}

export function PaneSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-0.5">
      {Array.from({ length: rows }, (_, index) => (
        <RowSkeleton key={index} />
      ))}
    </div>
  )
}
