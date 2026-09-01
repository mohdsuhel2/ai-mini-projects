# Simply Notes

A local-first daily planner and activity tracker. No account, no login, no
backend. Live at **[notes.noobius.in](https://notes.noobius.in)**.

Three ideas, two surfaces:

- **Things to do** — what you intend to do, on a day.
- **What I did** — what actually happened.
- **Notes** — everything worth keeping that has no deadline, in nested folders.

Todos live in time; notes live in space. A task with no date is still a task —
it goes to Someday, not to Notes.

Completing a task records it as an activity, so the timeline is the day as it
happened rather than the day as you planned it. That is what lets the app answer
*where did my day go?*

## Running it

```bash
npm install
npm run dev          # http://localhost:3000
```

| Script | Does |
|---|---|
| `npm run dev` | Development server |
| `npm run build` | Production build (standalone output) |
| `npm start` | Serve the production build |
| `npm test` | Vitest, once |
| `npm run test:watch` | Vitest, watching |
| `npm run lint` | ESLint, including the React Compiler rules |
| `npm run icons` | Regenerate PNG icons from `assets/*.svg` (needs `sharp`) |

## Configuration

Copy `.env.example` to `.env.local`. Both values are optional.

| Variable | Effect |
|---|---|
| `NEXT_PUBLIC_SITE_URL` | Canonical origin for metadata, sitemap and Open Graph |
| `NEXT_PUBLIC_GA_MEASUREMENT_ID` | Enables GA4. Unset means analytics never loads |

These are `NEXT_PUBLIC_*`, so they are inlined at build time — in Docker they are
build arguments, not runtime environment.

## Architecture

```
src/
  app/           routes, metadata, robots/sitemap/manifest
  components/    UI, grouped by feature area
  features/      data operations per domain (todos, activities, timer, …)
  lib/           db (Dexie), date, parsing, analytics, icons
  hooks/         data and environment hooks
  store/         ephemeral UI state only
```

Three decisions worth knowing before changing anything:

**IDs are UUID strings and every record carries `updatedAt` plus a soft-delete
`deletedAt`.** Nothing syncs today, but auto-increment keys would make a future
cloud sync impossible to merge without a rewrite. Reads filter out soft-deleted
rows; that is what makes Undo work.

**Calendar days are `YYYY-MM-DD` strings, never `Date` objects or UTC
timestamps.** Instants (`createdAt`, `completedAt`) are epoch milliseconds.
Mixing the two is the classic bug in a day-oriented app, and it is designed out
rather than guarded against.

**Folders nest through an adjacency list**, and two invariants are enforced in
code rather than assumed: a folder can never be moved inside its own descendant
(that detaches the subtree from every root and the notes inside become
unreachable), and deleting a folder cascades to its whole subtree in one
transaction so a single Undo restores it.

**Note bodies are markdown source**, rendered by `lib/markdown` — a small
renderer written for this app rather than a dependency. It escapes HTML
unconditionally, so raw HTML in a note can never execute, and anything outside
the supported set renders as literal text rather than disappearing.

**There is no global data store.** `useLiveQuery` makes IndexedDB itself the
reactive source of truth, so a write anywhere re-renders every reader with no
second copy to keep in step. `store/` holds only ephemeral UI state — open
dialogs, toasts, the selected pane.

Category colour is a *tone* — a hue — not a hex pair. One CSS rule computes
background, text and dot from `--tone-h` in oklch, with lightness and chroma
constants swapped per theme. Adding a category means adding a hue.

## Deployment

```bash
docker compose -f deploy/docker-compose.yml up -d --build   # -> :8085
```

Or from the monorepo root, `docker compose up -d notes`. `deploy/nginx-notes.conf`
is a ready reverse-proxy block for `notes.noobius.in`.

## Privacy

Everything a user writes stays in their browser's IndexedDB. There is no server
to send it to. Analytics, when configured, receives a fixed set of event names
and counts — never titles, notes or category names. It does not load at all when
Do Not Track is on.

Data is therefore only as durable as browser storage: Settings has JSON
export/import, which is also how you move between devices.

## Design record

`docs/superpowers/specs/2026-09-01-simply-notes-design.md`
