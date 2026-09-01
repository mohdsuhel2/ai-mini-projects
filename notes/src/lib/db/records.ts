import type { Instant } from '@/types'

export function newId(): string {
  return crypto.randomUUID()
}

export function now(): Instant {
  return Date.now()
}

/** Records carry a soft-delete marker so a future sync can propagate removals. */
export function isLive<T extends { deletedAt?: Instant | null }>(record: T): boolean {
  return record.deletedAt == null
}

export function liveOnly<T extends { deletedAt?: Instant | null }>(records: T[]): T[] {
  return records.filter(isLive)
}
