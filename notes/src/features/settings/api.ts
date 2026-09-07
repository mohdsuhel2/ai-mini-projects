import { db, SETTINGS_KEY } from '@/lib/db/database'
import { now } from '@/lib/db/records'
import type { Settings, ThemePreference } from '@/types'

export const DEFAULT_SETTINGS: Settings = {
  id: SETTINGS_KEY,
  theme: 'system',
  firstDayOfWeek: 1,
  onboarded: false,
  remindersEnabled: false,
  updatedAt: 0,
}

export async function getSettings(): Promise<Settings> {
  const row = await db().settings.get(SETTINGS_KEY)
  return row ?? DEFAULT_SETTINGS
}

export async function updateSettings(patch: Partial<Omit<Settings, 'id'>>): Promise<void> {
  const current = await getSettings()
  await db().settings.put({ ...current, ...patch, id: SETTINGS_KEY, updatedAt: now() })
}

export async function setThemePreference(theme: ThemePreference): Promise<void> {
  await updateSettings({ theme })
}
