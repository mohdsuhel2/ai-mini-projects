import { describe, expect, it, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { usePointerListReorder } from './use-pointer-list-reorder'

describe('usePointerListReorder', () => {
  it('commits the dragged id and insert position on pointer up', () => {
    const onCommit = vi.fn()
    const { result } = renderHook(() => usePointerListReorder(['a', 'b', 'c'], onCommit))

    act(() => {
      result.current.startDrag('b', 100)
    })

    act(() => {
      window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    })

    expect(onCommit).toHaveBeenCalledWith('b', null)
  })
})
