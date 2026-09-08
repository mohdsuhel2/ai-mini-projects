'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { modeToTab, parseTab, tabToMode, urlForTab, type Mode } from '@/lib/tab-url'

export type Pane = 'plan' | 'today'
export type QuickAddMode = 'todo' | 'activity'

/**
 * A create action that has to be carried out by a pane rather than a dialog:
 * the global add button can ask for it from anywhere, and whichever surface
 * owns that flow picks it up and clears it.
 */
export type ComposeIntent = 'timer' | 'note'

/**
 * The app has two surfaces. `day` is time — plan and timeline. `notes` is
 * space — a folder tree. The pane split only means anything inside `day`.
 * Which one is open lives in the URL, so a refresh lands where you were.
 */
export type { Mode }

export interface Toast {
  id: number
  message: string
  action?: { label: string; onClick: () => void }
}

interface UiContextValue {
  mode: Mode
  setMode: (mode: Mode) => void
  pane: Pane
  setPane: (pane: Pane) => void
  /** Switches to the day surface and selects a pane in one step. */
  showDay: (pane: Pane) => void
  quickAddOpen: boolean
  /** The mode the palette should open in, when the caller knows it. */
  quickAddMode: QuickAddMode | null
  openQuickAdd: (mode?: QuickAddMode) => void
  /** Set by the add button; consumed by the pane that owns the flow. */
  compose: ComposeIntent | null
  requestCompose: (intent: ComposeIntent) => void
  clearCompose: () => void
  closeQuickAdd: () => void
  settingsOpen: boolean
  openSettings: () => void
  closeSettings: () => void
  toasts: Toast[]
  notify: (message: string, action?: Toast['action']) => void
  dismissToast: (id: number) => void
}

const UiContext = createContext<UiContextValue | null>(null)

const TOAST_DURATION_MS = 5000

export function UiProvider({ children }: { children: ReactNode }) {
  // Seeded from the URL. The provider only ever mounts in the browser — the
  // app renders a skeleton until then — but the guard keeps this safe anywhere.
  const [mode, setModeState] = useState<Mode>(() =>
    typeof window === 'undefined' ? 'notes' : tabToMode(parseTab(window.location.search)),
  )
  const [pane, setPane] = useState<Pane>('plan')

  const setMode = useCallback((next: Mode) => {
    setModeState(next)
    // A pushed entry means Back returns to the previous tab, which is what a
    // tab addressable by URL should do.
    const target = urlForTab(window.location.href, modeToTab(next))
    if (target !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.pushState(null, '', target)
    }
  }, [])

  // Stamp the resolved tab onto the URL so even a bare "/" becomes shareable.
  // Read from a ref, not from `mode`: this must run once, because every later
  // change goes through setMode, which pushes a history entry instead.
  const initialMode = useRef(mode)
  useEffect(() => {
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`
    const target = urlForTab(window.location.href, modeToTab(initialMode.current))
    if (current !== target) window.history.replaceState(null, '', target)
  }, [])

  useEffect(() => {
    const onPopState = () => setModeState(tabToMode(parseTab(window.location.search)))
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])
  const [quickAddOpen, setQuickAddOpen] = useState(false)
  const [quickAddMode, setQuickAddMode] = useState<QuickAddMode | null>(null)
  const [compose, setCompose] = useState<ComposeIntent | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const notify = useCallback(
    (message: string, action?: Toast['action']) => {
      const id = nextId.current++
      setToasts((current) => [...current.slice(-2), { id, message, action }])
      window.setTimeout(() => dismissToast(id), TOAST_DURATION_MS)
    },
    [dismissToast],
  )

  const value = useMemo<UiContextValue>(
    () => ({
      mode,
      setMode,
      pane,
      setPane,
      showDay: (next: Pane) => {
        setMode('day')
        setPane(next)
      },
      quickAddOpen,
      quickAddMode,
      compose,
      requestCompose: setCompose,
      clearCompose: () => setCompose(null),
      openQuickAdd: (mode?: QuickAddMode) => {
        setQuickAddMode(mode ?? null)
        setQuickAddOpen(true)
      },
      closeQuickAdd: () => setQuickAddOpen(false),
      settingsOpen,
      openSettings: () => setSettingsOpen(true),
      closeSettings: () => setSettingsOpen(false),
      toasts,
      notify,
      dismissToast,
    }),
    [mode, setMode, pane, quickAddOpen, quickAddMode, compose, settingsOpen, toasts, notify, dismissToast],
  )

  return <UiContext.Provider value={value}>{children}</UiContext.Provider>
}

export function useUi(): UiContextValue {
  const value = useContext(UiContext)
  if (!value) throw new Error('useUi must be used inside <UiProvider>')
  return value
}
