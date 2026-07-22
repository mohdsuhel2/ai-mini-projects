# DocForge UI Redesign (Refine, Declutter, Responsive) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every DocForge page modern, decluttered, and reliably responsive while preserving the existing brand and all generator functionality.

**Architecture:** Static, no-build HTML/CSS/JS. Refine shared tokens in `css/site.css` and the generator's embedded `:root`; unify the generator's config-panel forms onto one collapsible-section pattern; add a mobile Edit⇄Preview toggle. All element `id`s and `data-*` hooks are preserved — markup is only re-wrapped and restyled.

**Tech Stack:** Vanilla HTML/CSS/JS, Geomini web font, existing vendor libs (pdf-lib, html2canvas, JSZip, Flatpickr). No new dependencies.

## Global Constraints

- **No brand change:** accent stays `#2563eb`; font stays `Geomini`; keep dark + light themes. Only spacing/shadow/hierarchy/focus refine.
- **Never rename or remove any `id` or `data-*` attribute** in `generator.html`. JS selects by them (`#amount`, `#layoutStyle`, `#bulkDateFrom`, `fuelGeneratorShell`, `data-view-mode`, `data-txn-mode`, etc.). Re-wrapping markup is allowed; changing identifiers is not.
- **Preserve field order and default values/placeholders/hints** within each logical group.
- **No new external dependencies or network calls.** Site stays no-build static.
- **Serve over HTTP for verification** (`python3 -m http.server 8765`), never `file://` (export/fonts require it).
- **Out of scope:** the generated document/receipt output templates (PNG/PDF artwork), vendor files, `index.html` (stays a redirect).

## Testing model (adapted — no test runner exists)

There is no unit-test framework. Each task's "test" is **browser-based behavioral verification** plus a **structural invariant check** (grep-based `id` count so we prove nothing was dropped). "Commit" steps assume git; **git is not yet initialized** — see Task 0. If the user declines git, treat each "Commit" step as "record verification passed and move on."

---

## Task 0: Baseline snapshot & optional git

**Files:**
- Modify: none (setup only)

**Interfaces:**
- Produces: `scratchpad/id-baseline.txt` — the authoritative list of every `id="..."` in `generator.html` before changes, used by later tasks to prove no id was lost.

- [ ] **Step 1: Capture the id baseline**

Run:
```bash
cd /Users/mohdsuhel/ai-mini-projects/document-generator
grep -oE 'id="[^"]+"' generator.html | sort > /private/tmp/claude-502/-Users-mohdsuhel-ai-mini-projects-document-generator/e93d5887-1005-42ce-ace8-fcb61688544b/scratchpad/id-baseline.txt
wc -l /private/tmp/claude-502/-Users-mohdsuhel-ai-mini-projects-document-generator/e93d5887-1005-42ce-ace8-fcb61688544b/scratchpad/id-baseline.txt
```
Expected: prints a count (record it — every later task must match this count).

- [ ] **Step 2: (Optional) Initialize git for checkpointing**

Only if the user approves. Run:
```bash
cd /Users/mohdsuhel/ai-mini-projects/document-generator
git init && printf 'node_modules\n.DS_Store\n' > .gitignore
git add -A && git commit -m "chore: baseline before UI redesign

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
Expected: initial commit created. If declined, skip — later "Commit" steps become no-ops.

- [ ] **Step 3: Start a local server for verification (leave running)**

Run in background:
```bash
cd /Users/mohdsuhel/ai-mini-projects/document-generator && python3 -m http.server 8765
```
Expected: serves at http://localhost:8765/ . Open `http://localhost:8765/fuel-receipt.html` — confirm it loads with no console errors as the reference baseline.

---

## Task 1: Shared foundation tokens

**Files:**
- Modify: `css/site.css` (`:root` / `[data-theme]` blocks, near lines 3–43)
- Modify: `generator.html` (`:root,[data-theme="dark"]` block, near lines 27–102)

**Interfaces:**
- Produces: CSS custom properties available site-wide and in the generator:
  `--space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 24px; --space-6: 32px;`
  `--radius-sm: 10px; --radius-md: 14px; --radius-lg: 18px;`
  `--shadow-1: 0 1px 2px rgba(15,23,42,.06); --shadow-2: 0 4px 16px rgba(15,23,42,.08); --shadow-3: 0 12px 32px rgba(15,23,42,.12);`
  `--focus-ring: 0 0 0 2px var(--body-bg), 0 0 0 4px var(--accent);`
  Later tasks reference these names.

- [ ] **Step 1: Add the token block to `css/site.css`**

Inside the existing `:root,[data-theme="dark"] { … }` block in `css/site.css` (after line 24, before the closing brace at line 25), add:
```css
  /* spacing / shape / elevation scale */
  --space-1: 4px;  --space-2: 8px;  --space-3: 12px;
  --space-4: 16px; --space-5: 24px; --space-6: 32px;
  --radius-sm: 10px; --radius-md: 14px; --radius-lg: 18px;
  --shadow-1: 0 1px 2px rgba(0, 0, 0, 0.18);
  --shadow-2: 0 8px 24px rgba(0, 0, 0, 0.22);
  --shadow-3: 0 18px 48px rgba(0, 0, 0, 0.35);
  --focus-ring: 0 0 0 2px var(--body-bg), 0 0 0 4px var(--accent);
```
Then inside `[data-theme="light"] { … }` (before its closing brace at line 43) add the light-theme elevation overrides:
```css
  --shadow-1: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-2: 0 8px 24px rgba(15, 23, 42, 0.08);
  --shadow-3: 0 12px 32px rgba(15, 23, 42, 0.12);
```

- [ ] **Step 2: Mirror the same tokens in `generator.html`**

Inside the generator's `:root,[data-theme="dark"] { … }` block (add before its closing brace near line 102), paste the same dark-theme token block from Step 1. Inside the generator's `[data-theme="light"] { … }` block (before its close near line 149) paste the same three light `--shadow-*` overrides.

- [ ] **Step 3: Add a global focus-visible rule to `css/site.css`**

At the end of `css/site.css` add:
```css
:where(a, button, input, select, textarea, summary, [tabindex]):focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  border-radius: 6px;
}
```

- [ ] **Step 4: Verify**

Reload `http://localhost:8765/about.html` and `http://localhost:8765/fuel-receipt.html` in both themes (use the header theme toggle). Expected: no visual regression, no console errors; Tab-key focus now shows a clear accent ring. Nothing else should change yet (tokens are defined but not widely consumed).

- [ ] **Step 5: Commit**
```bash
git add css/site.css generator.html
git commit -m "feat(ui): add shared spacing/shape/elevation/focus tokens

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Marketing pages refinement (about, features, 404, footers)

**Files:**
- Modify: `css/site.css` (`.section`, `.section-head`, `.feature-card`, `.detail-card`, `.step-card`, `.cta-inner`, `.site-footer*`, `.page-hero`)
- Modify: `features.html:119-128` (footer link list — dedupe)
- Modify: `about.html:130-139` (footer link list — align to shared set)
- Modify: `404.html` (inline `<style>` — use shared tokens)

**Interfaces:**
- Consumes: tokens from Task 1 (`--space-*`, `--radius-*`, `--shadow-*`).
- Produces: one canonical footer link set reused on every site page:
  `Generator` → `fuel-receipt.html`, `Features` → `features.html`, `About` → `about.html`.

- [ ] **Step 1: Apply tokens to marketing cards & sections in `css/site.css`**

Update these declarations to consume the scale (keep all other properties):
- `.feature-card`, `.detail-card`, `.step-card`: `border-radius: var(--radius-md);` and `box-shadow: var(--shadow-1);` and on `:hover` for `.feature-card` use `box-shadow: var(--shadow-2);`
- `.cta-inner`, `.hero-card`: `border-radius: var(--radius-lg);`
- `.section`: change bottom padding to `padding: var(--space-5) var(--space-5) var(--space-6);`
- `.section-head`: `margin: 0 auto var(--space-6);`

- [ ] **Step 2: Dedupe the features footer**

In `features.html` replace the footer links block (lines 122–127) with the canonical set:
```html
        <div class="site-footer-links">
          <a href="fuel-receipt.html">Generator</a>
          <a href="features.html">Features</a>
          <a href="about.html">About</a>
        </div>
```

- [ ] **Step 3: Align the about footer**

In `about.html` replace the footer links block (lines 133–137) with the identical canonical set from Step 2.

- [ ] **Step 4: Convert 404 inline style to tokens**

In `404.html` `<style>` block, change `.error-page` padding to `var(--space-6) var(--space-5)` and add `border-radius: var(--radius-lg);` only if a card wrapper is present (it is not — leave layout, just token the spacing). Keep the `4rem` heading.

- [ ] **Step 5: Verify**

Reload `about.html`, `features.html`, `404.html` at widths ≈1440, ≈768, ≈390 (browser devtools responsive mode), both themes. Expected: consistent card radii/shadows, tighter rhythm, footers show exactly three non-duplicated links, single-column stacking on mobile, no overflow, no console errors.

- [ ] **Step 6: Commit**
```bash
git add css/site.css about.html features.html 404.html
git commit -m "feat(ui): refine marketing pages spacing, cards, and footers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Generator — convert the fuel shell to collapsible sections

This is the highest-care task. The fuel shell (`#fuelGeneratorShell`) uses flat `.fuel-section-label` dividers while every other doc type uses `<details class="form-section">`. Convert fuel to match — **re-wrapping only, no inner edits.**

**Files:**
- Modify: `generator.html` — `#singleModePanel` (starts line 3154) and `#bulkModePanel` (starts ~3237) inside `#fuelGeneratorShell`.

**Interfaces:**
- Consumes: existing `.form-section` / `.form-section-title` / `.form-section-body` pattern (reference example at `generator.html:3377`).
- Produces: fuel form organized into the same accordion sections as other doc types. No new ids; all existing field `id`s unchanged.

**The wrapper pattern to apply** (reference, from the ecommerce shell):
```html
<details class="form-section" open>
  <summary class="form-section-title">SECTION NAME</summary>
  <div class="form-section-body">
    <!-- existing .field-group blocks, VERBATIM -->
  </div>
</details>
```

- [ ] **Step 1: Map the fuel SINGLE panel into sections**

Within `#singleModePanel`, group the existing consecutive `.field-group` blocks under each current `.fuel-section-label` into a `<details>` section. Delete the `<p class="fuel-section-label" …>` line and replace it with the `<details>`/`<summary>` opening; close the `</div></details>` before the next section. Sections (in existing order), with `open` state:

| Section (summary text) | Contains (existing field ids, unchanged) | Default |
|---|---|---|
| Appearance | `layoutStyle`, `bgTemplate` | `open` |
| Station & customer | `stationLine1`, `stationLine2`, `vehNo`, `customerName` | `open` |
| Transaction | the `.txn-mode-tabs` block, `amount`/`amountFieldGroup`, `fuelCapacity`/`capacityFieldGroup`, `rate` (+ its hint), `dateTime` | `open` |
| Receipt details | `receiptNo`, `vehType` | closed (no `open`) |

Rule: move the existing `.field-group` (and the `.txn-mode-tabs`) markup **verbatim** inside the new `.form-section-body`. Do not touch any inner `id`, `value`, `placeholder`, `label for`, or hint.

- [ ] **Step 2: Map the fuel BULK panel into sections**

Within `#bulkModePanel`, apply the same treatment. Group its existing `.field-group`s under `<details class="form-section">` sections by their current `.fuel-section-label` dividers (Date range / Batch / Transaction / Numbering, following the existing labels present in that panel). Keep the first one or two `open`, collapse advanced numbering/texture sections. Preserve every id (`bulkDateFrom`, `bulkDateTo`, `bulkAmount…`, etc.) verbatim.

- [ ] **Step 3: Structural invariant check — no id lost**

Run:
```bash
cd /Users/mohdsuhel/ai-mini-projects/document-generator
grep -oE 'id="[^"]+"' generator.html | sort > /private/tmp/claude-502/-Users-mohdsuhel-ai-mini-projects-document-generator/e93d5887-1005-42ce-ace8-fcb61688544b/scratchpad/id-after-t3.txt
diff /private/tmp/claude-502/-Users-mohdsuhel-ai-mini-projects-document-generator/e93d5887-1005-42ce-ace8-fcb61688544b/scratchpad/id-baseline.txt /private/tmp/claude-502/-Users-mohdsuhel-ai-mini-projects-document-generator/e93d5887-1005-42ce-ace8-fcb61688544b/scratchpad/id-after-t3.txt
```
Expected: **empty diff** (no ids added or removed). If diff is non-empty, a field was dropped or renamed — fix before proceeding.

- [ ] **Step 4: Behavioral verify — fuel single & bulk**

Reload `http://localhost:8765/fuel-receipt.html`:
- Single mode: every field from the table in Step 1 is present, editable, and updates the live preview. The Amount/Fuel-Capacity txn toggle still swaps fields. Sections expand/collapse on summary click.
- Switch to **Bulk Generate**: all bulk fields present and editable.
- Run one export (single receipt PNG) — confirm a PNG downloads.
Expected: all pass, no console errors.

- [ ] **Step 5: Commit**
```bash
git add generator.html
git commit -m "refactor(generator): convert fuel shell to collapsible sections

Re-wraps existing fields into <details> sections matching other doc types.
No id or field changes; verified via id-diff invariant.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Generator — unified section restyle, context header, mode toggle

**Files:**
- Modify: `generator.html` embedded CSS — `.form-section*` (lines 750–815), `.config-panel-header` (line 284), `.view-tabs-segment` (line 1122), `.generator-view-bar` region.
- Modify: `generator.html` markup — the `.generator-view-bar` (lines 3142–3148) to include a doc-type context label.

**Interfaces:**
- Consumes: Task 1 tokens; the sections created in Task 3.
- Produces: consistent section visuals across all 5 shells and a sticky context header showing doc type + Single/Bulk toggle.

- [ ] **Step 1: Refine `.form-section` visuals**

Update in the generator `<style>`:
- `.form-section`: `border-radius: var(--radius-sm); box-shadow: var(--shadow-1); margin-bottom: var(--space-2);`
- `.form-section[open]`: keep accent border; `box-shadow: var(--shadow-2);`
- `.form-section-title`: `padding: var(--space-3) var(--space-4); font-size: 0.85rem;` and add on `:hover` `background: var(--accent-soft);` for a clearer affordance. Keep the `::after` chevron.
- `.form-section-body`: `padding: var(--space-2) var(--space-4) var(--space-4);`

- [ ] **Step 2: Add a sticky doc-type context header to the view bar**

In `generator.html` replace the `.generator-mode-switcher` inner content (lines 3143–3147) so the bar shows the doc name on the left and the toggle on the right. The doc label text is populated by existing JS per shell; add a span with a stable id that JS can fill, defaulting to the fuel label:
```html
      <div class="generator-mode-switcher" id="generatorViewTabs">
        <span class="generator-doc-label" id="generatorDocLabel">Fuel Receipt</span>
        <div class="view-tabs-segment">
          <button type="button" class="mode-tab active" data-view-mode="single" id="generatorViewSingleTab" data-tab-icon="file">Single Receipt</button>
          <button type="button" class="mode-tab" data-view-mode="bulk" id="generatorViewBulkTab" data-tab-icon="layers">Bulk Generate</button>
        </div>
      </div>
```
Note: this **adds** a new id (`generatorDocLabel`) — additions are allowed; the invariant is that no existing id is removed. Do not require JS to populate it (static default text is acceptable); if a per-shell label map already exists in JS, optionally set `#generatorDocLabel.textContent` there.

- [ ] **Step 3: Style the context header & make the view bar sticky**

Add to the generator `<style>`:
```css
    .generator-view-bar {
      position: sticky;
      top: var(--site-header-height);
      z-index: 40;
    }
    .generator-mode-switcher {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3);
    }
    .generator-doc-label {
      font-weight: 700;
      font-size: 0.95rem;
      letter-spacing: -0.01em;
      color: var(--heading);
    }
```

- [ ] **Step 4: Structural invariant check**

Run the same id-diff as Task 3 Step 3 (new file `id-after-t4.txt`). Expected diff: **exactly one added line** `id="generatorDocLabel"`, nothing removed.

- [ ] **Step 5: Behavioral verify — all five doc types**

For each of `fuel-receipt.html`, `ecommerce-invoice.html`, `postpaid-bill.html`, `rent-receipt.html`, `driver-slip.html`: sections render with the unified style; hovering a summary highlights it; the sticky view bar (doc label + Single/Bulk) stays pinned under the header while scrolling the form; toggling Single/Bulk still switches panels. No console errors.

- [ ] **Step 6: Commit**
```bash
git add generator.html
git commit -m "feat(generator): unified section styling + sticky doc/mode header

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Generator — responsive Edit⇄Preview toggle + mid breakpoint

**Files:**
- Modify: `generator.html` markup — add a mobile Edit/Preview segmented control inside `.generator-view-bar`.
- Modify: `generator.html` embedded CSS — new `@media (max-width: 900px)` rules and a new `@media (max-width: 1200px)` block.
- Modify: `generator.html` inline `<script>` — small toggle handler (append near the existing view-mode wiring).

**Interfaces:**
- Consumes: `.app`, `.config-panel`, `.preview-area` layout.
- Produces: body state class `gen-mobile-view` with value on `#appRoot` via `data-mobile-view="edit|preview"`; both panels stay in the DOM.

- [ ] **Step 1: Add the mobile toggle markup**

Immediately after the `.view-tabs-segment` `</div>` inside `.generator-mode-switcher`, add a second segment shown only on small screens:
```html
        <div class="view-tabs-segment gen-mobile-toggle" role="tablist" aria-label="Edit or preview">
          <button type="button" class="mode-tab active" id="genMobileEditTab" data-mobile-view="edit">Edit</button>
          <button type="button" class="mode-tab" id="genMobilePreviewTab" data-mobile-view="preview">Preview</button>
        </div>
```

- [ ] **Step 2: Add the CSS — hide toggle on desktop, drive layout on mobile**

Add to the generator `<style>`:
```css
    .gen-mobile-toggle { display: none; }

    @media (max-width: 1200px) {
      :root { --config-panel-width: 420px; }
    }

    @media (max-width: 900px) {
      .gen-mobile-toggle { display: inline-flex; }
      #appRoot[data-mobile-view="edit"] .preview-area { display: none; }
      #appRoot[data-mobile-view="preview"] .config-panel { display: none; }
      #appRoot[data-mobile-view="preview"] .preview-area { display: flex; }
    }
```
(The existing ≤900px block already makes `.app` stack and `.config-panel` full-width; these rules add the mutual-exclusive toggle on top.)

- [ ] **Step 3: Initialize the default mobile state**

On `#appRoot`, add the default attribute in markup: change `<div class="app" id="appRoot">` to `<div class="app" id="appRoot" data-mobile-view="edit">`.

- [ ] **Step 4: Wire the toggle in JS**

Append this to the generator's inline `<script>` (after DOM ready / near other button wiring). It is self-contained and touches only the new elements:
```javascript
    (function initMobileViewToggle() {
      const root = document.getElementById('appRoot');
      const edit = document.getElementById('genMobileEditTab');
      const prev = document.getElementById('genMobilePreviewTab');
      if (!root || !edit || !prev) return;
      function set(view) {
        root.setAttribute('data-mobile-view', view);
        edit.classList.toggle('active', view === 'edit');
        prev.classList.toggle('active', view === 'preview');
      }
      edit.addEventListener('click', () => set('edit'));
      prev.addEventListener('click', () => set('preview'));
    })();
```

- [ ] **Step 5: Structural invariant check**

Run the id-diff (`id-after-t5.txt`) vs the Task 4 result. Expected: only the two new ids `genMobileEditTab`, `genMobilePreviewTab` added; none removed.

- [ ] **Step 6: Behavioral verify — responsive toggle**

In devtools responsive mode at ≈390px width, for `fuel-receipt.html` and one other doc type (e.g. `ecommerce-invoice.html`):
- Default shows the **Edit** form; **Preview** button switches to the live preview; **Edit** switches back. Both retain state (edits made in Edit appear in Preview).
- At ≥901px the mobile toggle is hidden and the normal side-by-side layout is intact; the resizer works.
- At ≈1000–1200px the panel narrows gracefully with no overlap.
Expected: all pass, no console errors, exports still work.

- [ ] **Step 7: Commit**
```bash
git add generator.html
git commit -m "feat(generator): mobile Edit/Preview toggle + mid breakpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Full verification pass

**Files:** none (verification only).

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Final id invariant vs baseline**

Run:
```bash
cd /Users/mohdsuhel/ai-mini-projects/document-generator
grep -oE 'id="[^"]+"' generator.html | sort > /private/tmp/claude-502/-Users-mohdsuhel-ai-mini-projects-document-generator/e93d5887-1005-42ce-ace8-fcb61688544b/scratchpad/id-final.txt
comm -23 /private/tmp/claude-502/-Users-mohdsuhel-ai-mini-projects-document-generator/e93d5887-1005-42ce-ace8-fcb61688544b/scratchpad/id-baseline.txt /private/tmp/claude-502/-Users-mohdsuhel-ai-mini-projects-document-generator/e93d5887-1005-42ce-ace8-fcb61688544b/scratchpad/id-final.txt
```
Expected: **empty output** (no baseline id was removed). Added ids are fine.

- [ ] **Step 2: Matrix verification**

For each doc type × {Single, Bulk} × {light, dark} × {≈1440, ≈768, ≈390}px:
- All fields present and editable; preview updates live.
- One export per doc type succeeds (Fuel→PNG, others→PDF; try one bulk ZIP).
- Mobile Edit⇄Preview works; no horizontal overflow; no console errors.

Record results as a short checklist in the commit message.

- [ ] **Step 3: Cross-page sweep**

Load `about.html`, `features.html`, `404.html`, and confirm `index.html` still redirects to `fuel-receipt.html`. Both themes, mobile + desktop. No regressions.

- [ ] **Step 4: Final commit**
```bash
git add -A
git commit -m "test(ui): full verification pass — all doc types, themes, breakpoints

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** foundation tokens → T1; marketing refine + footer dedupe → T2; fuel accordion unify → T3; unified section restyle + context header + mode toggle → T4; responsive Edit⇄Preview + mid breakpoint → T5; verification matrix → T6. Navigation active-state and mobile drawer are inherited from existing `site.css`/`site.js` and re-verified in T2/T6. All spec sections mapped.
- **Id safety:** every generator-touching task runs a grep id-diff; the only allowed change is *additions* (`generatorDocLabel`, `genMobileEditTab`, `genMobilePreviewTab`).
- **No placeholders:** all new CSS/JS is provided inline; markup re-wrapping uses a worked pattern + explicit field/id mapping table.
