# Notes and Folders — Design

Date: 2026-09-01
Status: Approved
Extends: `2026-09-01-simply-notes-design.md`

## 1. The split

Todos live in **time**: they carry a date, they appear on a day, they roll over
when missed. Notes live in **space**: they carry no date at all and are placed
in a folder tree.

Adding notes also forces a gap in the existing model to close — a task you want
to do "sometime" has nowhere to go today, because `plannedDate` is required.
That becomes nullable here.

## 2. Data model

```ts
Folder { id, name, parentId: Id | null, order, createdAt, updatedAt, deletedAt }
Note   { id, title, body, folderId: Id | null, createdAt, updatedAt, deletedAt }
```

Nesting is an adjacency list. `parentId: null` means root; depth is unbounded,
so `Work → Service 1 → Deploy` nests as far as the user wants.

Two invariants the code enforces:

- **No cycles.** A folder cannot be moved into its own descendant. Without this
  a subtree detaches from the root and becomes unreachable — the records still
  exist, but nothing can ever display them again.
- **Delete cascades.** Removing a folder soft-deletes its entire subtree,
  folders and notes together, in one transaction. A single Undo restores it.

Notes carry folders and **not** categories. Categories exist to attribute time,
which is exactly the thing notes do not have; offering both would be two
competing organisers for one object.

`body` is markdown **source**, stored as plain text. It stays readable in an
export, in a diff, and in any editor the user ever moves to.

## 3. Undated todos

`Todo.plannedDate` becomes `DayKey | null` under Dexie version 2. Existing rows
are untouched by the migration.

IndexedDB cannot index a null key, so an undated todo simply does not appear in
`plannedDate` index queries. That is the behaviour we want and it needs no
special case: an undated task cannot surface on a day, in a timeline, or in a
day's summary, because those all query by day.

Grouping gains a **Someday** bucket below Upcoming. The date chips gain a "No
date" option. `rollOverOverdue` skips undated todos rather than comparing null.

## 4. Navigation

The app gains a `mode` above the existing pane split:

| Mode | Surface |
|---|---|
| `day` | The current Plan + Today two-pane surface, unchanged. |
| `notes` | Folder tree plus note editor. |

Desktop puts a `Day / Notes` segmented control in the header. The mobile bottom
bar becomes `Plan · Notes · + · Today`.

## 5. The Notes surface

Two columns, not three: **notes appear inside the tree** as leaves under their
folder, rather than getting a separate list column. This matches how the user
described the problem — `Work → Service 1` — and leaves the note itself wide
enough to actually write in.

On mobile the tree is the screen; opening a note pushes a full-screen editor.

Moving a note or folder uses a **"Move to…" picker**, not drag-and-drop: it
works on a phone, it works from the keyboard, and it is a fraction of the cost.

A filter above the tree matches note titles and bodies. Once a tree is three
levels deep, browsing alone stops being enough to find anything.

## 6. The editor

**One live surface**, not a source view with a preview. What you type is already
formatted; there is no mode to flip. A toolbar above the body applies headings,
bold, italic, strikethrough, bulleted and numbered lists and quotes to the
selection, and `⌘B` / `⌘I` work as expected.

What gets **stored is still markdown**. The DOM is serialised back on every
change by `lib/markdown/serialize`, so notes stay portable, exportable and
readable outside this app, and the rendering path is unchanged. The supported
set is deliberately small, which is what makes the round trip reliable: render →
edit → serialise → render returns the same document.

Formatting is applied with `document.execCommand`. It is deprecated and every
browser still implements it; writing a selection-and-range engine instead would
be a great deal of code to reproduce behaviour that already works everywhere.

Paste is forced to plain text. Pasted markup would put tags in the note that the
serialiser cannot represent and the renderer would escape on the next load.

Writes autosave on a debounce — with no server, a Save button would be theatre.

### Markdown rendering

A small renderer written for this app rather than `marked` + `DOMPurify`.

It supports headings, bold, italic, inline code, fenced code, ordered and
unordered lists, task lists, blockquotes, horizontal rules and links — and it
**escapes HTML unconditionally**, so raw HTML in a note body can never execute.
That is a stronger guarantee than sanitising a general parser's output after the
fact, and it removes two dependencies.

The tradeoff is real: hand-rolled markdown is a known source of edge-case bugs.
It is mitigated by writing it test-first and keeping the supported set small and
explicit. Anything unsupported renders as literal text, never as markup.

## 7. Backup

`BackupFile` version 2 carries `notes` and `folders`. A version 1 backup still
imports: absent tables read as empty, which the existing parser already handles.

## 8. Testing

Test-first for the two pure modules — the markdown renderer, and tree building
with cycle detection. Then integration tests against `fake-indexeddb` for
folder and note CRUD, cascade delete, undated-todo grouping, and importing a
version 1 backup into a version 2 database.

## 9. Creation target

The tree carries an **All notes** root row and a selected folder. New notes and
folders go into whatever is selected, and the toolbar states the destination in
words rather than leaving it implied.

Clicking a folder's **name** selects it as that destination; the **chevron** is a
separate target that only expands. Merging the two would make it impossible to
choose a collapsed folder without opening it.

## 10. Deleting from the keyboard

With a note or folder selected, `D` or `Backspace` asks before deleting, and
`Shift+Backspace` deletes straight away. The unprompted path is only safe to
offer because every delete is soft and the toast carries Undo.

Deleting a folder names how many notes go with it, because the blast radius is
wider than the row that was clicked.

## 11. Moving things

Three routes to the same operation, because each fails somewhere the others do
not:

- **Drag and drop** onto any folder row, or onto **All notes** to move to the
  root. Pointer only.
- **Right-click** (or the row's ⋯ button) for Open, Duplicate, Move to… and
  Delete. Works from a keyboard and on a phone.
- **Move to…** opens the picker, which greys out destinations that would create
  a cycle.

Drags carry a private MIME type, so the tree only accepts drags that came from
the tree — a file dragged in from the desktop is ignored rather than parsed into
a nonsense move. Cycle prevention runs on drop as well as in the picker.

## 12. Duplicating

Duplicating a note copies it into the same folder. Duplicating a folder
deep-copies its whole subtree and every note inside, in one transaction, with
freshly minted ids throughout — editing a copy must never change the original.

## 13. Todo grouping

Todos bucket by how soon they are: **Overdue, Today, Tomorrow, Later this week,
Next week, Upcoming, Someday**. Week boundaries come from the calendar week and
respect `firstDayOfWeek`. Empty buckets are dropped, except Today.

## 14. Deliberately excluded

No Note mode in quick-add; notes are created deliberately. No per-note markdown
file export. No linking notes to tasks. Each is cheap to add later and none
earns its complexity now.
