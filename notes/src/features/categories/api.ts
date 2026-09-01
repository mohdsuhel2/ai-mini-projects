import { db } from '@/lib/db/database'
import { liveOnly, newId, now } from '@/lib/db/records'
import type { Category, CategoryScope, Id, Tone } from '@/types'

export async function listCategories(): Promise<Category[]> {
  const all = await db().categories.toArray()
  return liveOnly(all).sort((a, b) => a.order - b.order || a.name.localeCompare(b.name))
}

export function categoriesForScope(categories: Category[], scope: 'todo' | 'activity'): Category[] {
  return categories.filter((c) => c.scope === 'both' || c.scope === scope)
}

export async function createCategory(input: {
  name: string
  icon: string
  tone: Tone
  scope: CategoryScope
}): Promise<Id> {
  const stamp = now()
  const existing = await db().categories.toArray()
  const maxOrder = existing.reduce((max, c) => Math.max(max, c.order), 0)
  const id = newId()
  await db().categories.add({
    id,
    name: input.name.trim(),
    icon: input.icon,
    tone: input.tone,
    scope: input.scope,
    isDefault: false,
    order: maxOrder + 10,
    createdAt: stamp,
    updatedAt: stamp,
    deletedAt: null,
  })
  return id
}

export async function updateCategory(id: Id, patch: Partial<Omit<Category, 'id'>>): Promise<void> {
  await db().categories.update(id, { ...patch, updatedAt: now() })
}

/**
 * Soft-deletes the category and detaches it from anything referencing it, so a
 * removed category never leaves a dangling badge behind.
 */
export async function deleteCategory(id: Id): Promise<void> {
  const stamp = now()
  await db().transaction('rw', db().categories, db().todos, db().activities, async () => {
    await db().categories.update(id, { deletedAt: stamp, updatedAt: stamp })
    const todos = await db().todos.where('categoryId').equals(id).toArray()
    await Promise.all(
      todos.map((t) => db().todos.update(t.id, { categoryId: null, updatedAt: stamp })),
    )
    const activities = await db().activities.where('categoryId').equals(id).toArray()
    await Promise.all(
      activities.map((a) => db().activities.update(a.id, { categoryId: null, updatedAt: stamp })),
    )
  })
}
