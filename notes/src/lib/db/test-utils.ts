import { db, resetDatabaseForTests } from './database'

/** Drops and reopens the database so each test starts from a seeded blank slate. */
export async function freshDatabase(): Promise<void> {
  await db().delete()
  resetDatabaseForTests()
  await db().open()
}
