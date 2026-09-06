import { describe, expect, it } from 'vitest'
import { formatWallClock } from './format'

const at = (h: number, m: number, s: number) => new Date(2026, 8, 3, h, m, s)

describe('formatWallClock', () => {
  it('reads as a 12-hour clock with zero-padded minutes and seconds', () => {
    expect(formatWallClock(at(18, 44, 9))).toBe('6:44:09 PM')
    expect(formatWallClock(at(9, 5, 30))).toBe('9:05:30 AM')
  })

  it('calls both twelves twelve, not zero', () => {
    expect(formatWallClock(at(0, 0, 0))).toBe('12:00:00 AM')
    expect(formatWallClock(at(12, 0, 0))).toBe('12:00:00 PM')
  })

  it('flips to PM at noon and back at midnight', () => {
    expect(formatWallClock(at(11, 59, 59))).toBe('11:59:59 AM')
    expect(formatWallClock(at(23, 59, 59))).toBe('11:59:59 PM')
  })
})
