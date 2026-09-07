/**
 * Every persisted record is addressed by a UUID string, never an
 * auto-increment key: a future cloud sync has to merge records created on two
 * devices that never saw each other's counters.
 */
export type Id = string

/** A local calendar day, `YYYY-MM-DD`. Never a Date, never UTC. */
export type DayKey = string

/** Minutes past local midnight, 0–1439. */
export type MinuteOfDay = number

/** Epoch milliseconds. Used only for instants, never for calendar days. */
export type Instant = number

export type Tone =
  | 'blue'
  | 'violet'
  | 'rose'
  | 'orange'
  | 'amber'
  | 'emerald'
  | 'teal'
  | 'cyan'
  | 'indigo'
  | 'pink'
  | 'lime'
  | 'red'
  | 'slate'

export type CategoryScope = 'both' | 'todo' | 'activity'

export interface Category {
  id: Id
  name: string
  /** Key into the icon registry in `lib/icons`. */
  icon: string
  tone: Tone
  scope: CategoryScope
  isDefault: boolean
  /** Sort position; defaults are spaced by 10 so custom ones can slot between. */
  order: number
  createdAt: Instant
  updatedAt: Instant
  deletedAt?: Instant | null
}

/** 0 is Sunday, matching `Date.getDay`. */
export type Weekday = 0 | 1 | 2 | 3 | 4 | 5 | 6

/**
 * How a task repeats. Weekly carries a set of days, so "Mon, Wed and Fri" is
 * one task rather than three; `daily` stays its own kind because "every day"
 * is a different thing to say than "all seven of these days".
 */
export type Recurrence =
  | { kind: 'daily' }
  | { kind: 'weekly'; weekdays: Weekday[] }

export type TodoStatus = 'OPEN' | 'COMPLETED'

export interface Todo {
  id: Id
  title: string
  categoryId?: Id | null
  /**
   * Null means "someday" — a task worth keeping without a date attached.
   * IndexedDB cannot index a null key, so undated todos are simply absent from
   * every by-day query, which is exactly the behaviour a dateless task needs.
   */
  plannedDate: DayKey | null
  /** Optional clock time for the task, minutes past midnight. */
  plannedTime?: MinuteOfDay | null
  /** Optional "this should take about N minutes". */
  estimatedDuration?: number | null
  status: TodoStatus
  /** Absent on a one-off task, which is almost all of them. */
  recurrence?: Recurrence | null
  /**
   * Ties the occurrences of one repeating task together. Only set alongside
   * `recurrence`, and kept by every occurrence the series spawns.
   */
  seriesId?: Id | null
  notes?: string | null
  createdAt: Instant
  completedAt?: Instant | null
  /** Minutes actually spent, if the user recorded or timed it. */
  actualDuration?: number | null
  order: number
  updatedAt: Instant
  deletedAt?: Instant | null
}

export type ActivitySource = 'MANUAL' | 'TODO_COMPLETION' | 'TIMER'

export interface Activity {
  id: Id
  title: string
  categoryId?: Id | null
  date: DayKey
  /** Minutes past midnight the activity began. */
  startTime?: MinuteOfDay | null
  endTime?: MinuteOfDay | null
  /** Minutes. The single source of truth for "how long"; times are context. */
  duration?: number | null
  source: ActivitySource
  todoId?: Id | null
  notes?: string | null
  createdAt: Instant
  updatedAt: Instant
  deletedAt?: Instant | null
}

/**
 * Folders nest through an adjacency list. `parentId: null` is a root folder;
 * depth is unbounded.
 */
export interface Folder {
  id: Id
  name: string
  parentId: Id | null
  order: number
  createdAt: Instant
  updatedAt: Instant
  deletedAt?: Instant | null
}

export interface Note {
  id: Id
  title: string
  /** Markdown source, stored as plain text so it survives any export. */
  body: string
  /** Null means the note sits at the root, outside any folder. */
  folderId: Id | null
  createdAt: Instant
  updatedAt: Instant
  deletedAt?: Instant | null
}

/** A folder with its children resolved, ready to render as an outline. */
export interface FolderNode {
  folder: Folder
  depth: number
  children: FolderNode[]
  notes: Note[]
}

export type ThemePreference = 'light' | 'dark' | 'system'

export interface Settings {
  /** Single-row table; the key is always `SETTINGS_KEY`. */
  id: string
  theme: ThemePreference
  firstDayOfWeek: 0 | 1
  /** Whether the user has dismissed the first-run hint. */
  onboarded: boolean
  /**
   * Opt-in, and off until asked for. Reminders need notification permission,
   * and a prompt nobody invited is a prompt people block for good.
   */
  remindersEnabled?: boolean
  updatedAt: Instant
}

export interface TimerState {
  /** Single-row table; the key is always `TIMER_KEY`. */
  id: string
  title: string
  todoId?: Id | null
  categoryId?: Id | null
  /** When the current run segment began. Null while paused. */
  runningSince: Instant | null
  /** Milliseconds banked from previous run segments. */
  accumulatedMs: number
  startedAt: Instant
  updatedAt: Instant
}

/** A todo and an activity rendered on the same vertical line. */
export type TimelineEntry =
  | { kind: 'activity'; at: MinuteOfDay | null; activity: Activity }
  | { kind: 'planned'; at: MinuteOfDay; todo: Todo }

export interface CategoryTotal {
  categoryId: Id | null
  name: string
  tone: Tone
  icon: string
  minutes: number
}

export interface DailySummary {
  day: DayKey
  completedCount: number
  openCount: number
  trackedMinutes: number
  activityCount: number
  byCategory: CategoryTotal[]
}

export interface BackupFile {
  format: 'simply-notes-backup'
  version: number
  exportedAt: string
  counts: Record<string, number>
  data: {
    todos: Todo[]
    activities: Activity[]
    categories: Category[]
    settings: Settings[]
    /** Absent in version 1 backups; read as empty. */
    notes: Note[]
    folders: Folder[]
  }
}
