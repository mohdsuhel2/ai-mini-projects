# Simply Notes — Design

Date: 2026-09-01
Status: Approved
Site: https://notes.noobius.in (brand domain: https://noobius.in)

## 1. Product

A local-first daily planner and activity tracker. No account, no login, no
backend. The user opens the page and starts working. Every byte of user data
stays in the browser's IndexedDB.

Two ideas, one surface:

- **Things to do** — what the user intends to do.
- **What I did** — what actually happened.

The product's job is to make the second column answer "where did my day go?"

## 2. Information architecture

| Route | Rendering | Purpose |
|---|---|---|
| `/` | Server shell + client app | The application. Below the fold, server-rendered semantic content (What is Simply Notes, Features, Privacy, FAQ). |
| `/privacy` | Static | Privacy statement. |
| `/robots.txt`, `/sitemap.xml`, `/manifest.webmanifest` | Next metadata routes | Crawling and installability. |

The SEO content sits beneath the app viewport. It is real HTML in the initial
response — crawlable without a marketing site and without intruding on the
tool.

## 3. Layout

Width decides the shape. The segmented control exists only where both panes
cannot fit at once.

- **>= 1100px** — two columns. Left: Plan (composer + grouped todos). Right:
  Today (summary, timeline, activity composer). No segmented control.
- **640–1099px** — one column, segmented control in the header.
- **< 640px** — one column, bottom navigation `Plan | + | Today`. The centre
  button opens quick-add as a bottom sheet.

Completing a todo animates it out of the Plan column and into the timeline.
That transition is the argument for the two-column layout: it makes the link
between intention and record literal rather than conceptual.

The Today pane steps through days with `<` `>` so reflection works for
yesterday too.

## 4. Visual system

The palette is burnt sienna. The field is cream and beige, every neutral —
background, surfaces, borders and the whole text ramp — sits on a warm hue, and
the accent is a burnt sienna coral. Dark mode is a warm sienna charcoal, never
black or blue-grey.

Category colour now has three steps rather than one: a soft fill for badges that
label something, a **solid** fill for the chip you press to choose it, and a dot
for legends. All three are computed from the same `--tone-h`. The solid step is
tuned to the lightest hue in the set rather than the average, because green and
cyan carry less contrast than red at the same oklch lightness.

Type carries weight where weight means something: bold uppercase eyebrows,
bold group headings, semibold numerals for durations and the day's total,
and 450 on task titles so they sit above their metadata without becoming
headings. The rest stays quiet.

**The day bar spans 24 hours, not the tracked total.** A bar scaled to what was
logged always looks full, which is exactly the question it is meant not to
answer: forty logged minutes should read as forty minutes out of a day. The
remainder is grey and labelled "unaccounted".

**Choosing a category is required** when adding a task or logging an activity.
That is a deliberate trade of a little capture speed for a complete record —
without it the day bar fills with "Other" and stops being worth reading. The
categories are therefore on the surface as solid chips rather than behind a
popover: a required field hidden in a menu is a required field people abandon.

**Signature — the day's thread.** The timeline rail is not one grey line. Each
entry owns its segment and colours it with its own category tone, so the thread
running down the day is a picture of how the day was actually spent, on the same
axis as the entries themselves. Rendering the segment inside each row also means
it always matches that row's real height instead of a guessed one.

Design tokens live as CSS custom properties and are mapped into Tailwind v4 via
`@theme inline`, so components never write a `dark:` variant — the variables
change, the markup does not.

Category colour is a **tone** (a hue) rather than a hex pair. One rule computes
background, foreground and dot from `--tone-h` in oklch, with the lightness and
chroma constants swapped per theme. Adding a category means adding a hue.

Every step of the muted text ramp carries real text somewhere in the UI, so all
four clear WCAG AA (4.5:1) against both `--bg` and `--surface`, in both themes —
measured by sampling painted pixels in a real browser, not estimated from the
oklch numbers. Hairline row borders are decorative and deliberately below 3:1;
the checkbox, whose outline *is* the control, gets its own `--control-line`
token that clears it.

Constraints: radius 6/8/10 only; a single shadow token, reserved for popovers,
dialogs and sheets; rows get their hover state from background and border alone.
Inter is self-hosted through `next/font` — no Google request, no layout shift,
works offline.

## 5. Data

Dexie over IndexedDB. Tables: `todos`, `activities`, `categories`, `settings`,
`timer`.

Three decisions that are hard to reverse later:

1. **IDs are `crypto.randomUUID()` strings.** Auto-increment integers make a
   future cloud-sync merge impossible without a rewrite.
2. **Every record carries `updatedAt` and a soft-delete `deletedAt`.** This is
   the difference between sync being a feature and sync being a migration. It
   costs one field today.
3. **`plannedDate` and `activity.date` are `YYYY-MM-DD` local calendar
   strings.** Never a `Date`, never a UTC timestamp. Instants (`createdAt`,
   `completedAt`, `startedAt`) are epoch milliseconds. Mixing the two is the
   most common bug class in a day-oriented app and it is designed out here.

Reactivity comes from `useLiveQuery` (`dexie-react-hooks`), making IndexedDB the
reactive source of truth directly. No Redux or Zustand. A small React context
holds only ephemeral UI state: theme, open dialogs, toasts.

The active focus timer is persisted in the `timer` table, not component state,
so it survives reload and tab close.

### Todo to Activity

Completion runs in a single Dexie transaction: set `status = COMPLETED` and
`completedAt`, then insert an Activity carrying `todoId` and
`source = TODO_COMPLETION`. Un-completing deletes that linked activity — even if
the user edited it. Predictable beats clever. Manual activities are never
touched by todo state.

## 6. Natural language parsing

A pure, dependency-free module. No AI.

- Durations: `for 1 hour`, `1h 30m`, `45 min`, `90m`.
- Dates: `today`, `tomorrow`, `tonight`, `this weekend`, weekday names.
- Times: `at 6pm`, `6:30 PM`, `18:00`.

Matched tokens are stripped from the title. Quick-add renders a live preview of
what was parsed, so the parser never guesses silently. A duration or past-tense
phrasing defaults the palette to Activity; otherwise Todo. Tab flips it.

This is the highest-risk module in the app, so it is written test-first.

## 7. Testing

- **Vitest** — the parser, date utilities, duration formatting, daily-summary
  aggregation, and the database transitions against `fake-indexeddb`.
- Four flows must never break: add -> complete -> appears in timeline; log a
  manual activity; timer start -> complete; export -> clear -> import round-trip.

## 8. Analytics

`lib/analytics.ts` exposes `track(event, params?)` over a fixed event enum. It
no-ops entirely when `NEXT_PUBLIC_GA_MEASUREMENT_ID` is unset or when Do Not
Track is on. Only counts and enum values leave the device — never titles, notes,
or category contents. Loaded `afterInteractive` so it cannot affect LCP.

No cookie-consent banner. If the site draws meaningful EU traffic, GA4 without
consent is a compliance gap that needs closing.

## 9. Offline

A hand-written service worker, no `next-pwa`. Hashed `/_next/static/*` is
cache-first (immutable by construction). Navigations are network-first with a
short timeout falling back to the cached shell, so an online user never gets
stale HTML and an offline user always gets the app.

## 10. Deployment

`deploy/Dockerfile` — multi-stage, `output: 'standalone'`, `node:22-alpine`,
non-root, port 3000. `deploy/docker-compose.yml` for the project and a `notes`
service on host port 8085 in the root `docker-compose.yaml` (8084 is taken by
`document-generator`). A sample nginx server block for `notes.noobius.in` ships
alongside. This mirrors the existing `document-generator/deploy/` convention.

## 11. Build order

1. Tokens, primitives, Dexie layer, seeds.
2. Todos: compose, categorise, schedule, complete.
3. Activities and the timeline.
4. Durations, focus timer, daily summary.
5. Dark mode, shortcuts, quick-add, settings, export/import, PWA.
6. SEO, JSON-LD, analytics.
7. Docker, tests green.

## 12. Deliberate exclusions

No login, no backend, no cloud sync, no AI, no charts library, no onboarding
flow, no confetti. Custom categories exist but live in Settings, off the main
surface.
