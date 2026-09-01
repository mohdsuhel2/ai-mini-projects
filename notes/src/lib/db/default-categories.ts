import type { Category, CategoryScope, Tone } from '@/types'

/**
 * Seeded on first run. Ordering is spaced by 10 so a custom category can be
 * slotted between two defaults without renumbering the world.
 */
export interface CategorySeed {
  id: string
  name: string
  icon: string
  tone: Tone
  scope: CategoryScope
}

export const DEFAULT_CATEGORIES: CategorySeed[] = [
  { id: 'cat-work', name: 'Work', icon: 'briefcase', tone: 'blue', scope: 'both' },
  { id: 'cat-personal', name: 'Personal', icon: 'user', tone: 'violet', scope: 'both' },
  { id: 'cat-health', name: 'Health', icon: 'heart-pulse', tone: 'rose', scope: 'both' },
  { id: 'cat-fitness', name: 'Fitness', icon: 'dumbbell', tone: 'orange', scope: 'both' },
  { id: 'cat-learning', name: 'Learning', icon: 'book-open', tone: 'amber', scope: 'both' },
  { id: 'cat-finance', name: 'Finance', icon: 'wallet', tone: 'emerald', scope: 'both' },
  { id: 'cat-shopping', name: 'Shopping', icon: 'shopping-bag', tone: 'teal', scope: 'both' },
  { id: 'cat-family', name: 'Family', icon: 'home', tone: 'cyan', scope: 'both' },
  { id: 'cat-social', name: 'Social', icon: 'message-circle', tone: 'indigo', scope: 'both' },
  { id: 'cat-entertainment', name: 'Entertainment', icon: 'play', tone: 'pink', scope: 'both' },
  { id: 'cat-travel', name: 'Travel', icon: 'plane', tone: 'lime', scope: 'both' },
  { id: 'cat-important', name: 'Important', icon: 'flag', tone: 'red', scope: 'todo' },
  { id: 'cat-food', name: 'Food', icon: 'utensils', tone: 'orange', scope: 'activity' },
  { id: 'cat-social-media', name: 'Social Media', icon: 'smartphone', tone: 'pink', scope: 'activity' },
  { id: 'cat-relaxation', name: 'Relaxation', icon: 'coffee', tone: 'teal', scope: 'activity' },
  { id: 'cat-sleep', name: 'Sleep', icon: 'moon', tone: 'indigo', scope: 'activity' },
]

export function buildDefaultCategories(now: number): Category[] {
  return DEFAULT_CATEGORIES.map((seed, index) => ({
    ...seed,
    isDefault: true,
    order: (index + 1) * 10,
    createdAt: now,
    updatedAt: now,
    deletedAt: null,
  }))
}
