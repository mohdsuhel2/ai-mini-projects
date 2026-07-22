# DocForge UI Redesign — Refine, Declutter, Responsive

**Date:** 2026-07-20
**Status:** Design — awaiting approval

## Goal

Make every page of the DocForge static site feel modern, less cluttered, more
user-friendly, and reliably responsive — **without changing the existing brand
identity** (blue accent `#2563eb`, Geomini typeface, dark/light themes) and
**without breaking any generator functionality** (all field `id`s, `data-*`
attributes, and JS wiring preserved).

This is a *refinement and declutter* pass, not a rebrand. Generator app is the
priority; marketing pages second.

## Scope

In scope:
- `generator.html` (all 5 doc-type shells: fuel, ecommerce/invoice, postpaid, rent, driver; single + bulk modes)
- `css/site.css` (shared shell)
- `about.html`, `features.html`, `404.html`
- Shared tokens/foundation used by both the site shell and the generator's embedded CSS

Out of scope:
- `index.html` stays an instant redirect to `fuel-receipt.html` (no landing page)
- Document/receipt *output* templates (the generated PNG/PDF artwork) — untouched; only the app chrome around them changes
- Third-party vendor files (pdf-lib, html2canvas, jszip)
- Any backend/build step (site remains no-build static)

## Non-negotiable constraints

1. **Preserve all element `id`s and `data-*` attributes** in `generator.html`. JS
   selects by id (`#amount`, `#layoutStyle`, `#bulkDateFrom`, shell ids like
   `fuelGeneratorShell`, etc.). Re-wrapping markup is allowed; renaming/removing
   ids is not.
2. **Preserve field order and behavior** within each logical group.
3. **All 5 doc types + single/bulk + export (PNG/PDF/ZIP)** must still work after
   every change. Verified per doc type.
4. No new external dependencies or network calls.

## Design

### 1. Shared foundation (tokens)

Introduce a consistent, small set of design tokens reused by both `site.css` and
the generator's embedded `:root`:
- Spacing scale (4/8/12/16/24/32) applied to field gaps, section padding, card padding.
- Slightly softer, unified card radii (14–16px) and lighter, layered shadows.
- Tightened type scale with consistent label/hint sizes.
- Unified focus ring (accent, 2px, visible) for keyboard accessibility.

No changes to color hues, fonts, or the light/dark palettes beyond spacing/shadow polish.

### 2. Generator config panel — unified collapsible sections (primary declutter)

**Problem:** Fuel shell uses flat text dividers (`.fuel-section-label`), while
ecommerce/rent/driver/postpaid use collapsible `<details class="form-section">`.
Inconsistent, and everything is expanded → a long wall of fields.

**Change:**
- Convert the **fuel** shell (single + bulk panels) to the same
  `<details class="form-section">` accordion pattern as the other doc types.
  Group into: **Appearance**, **Station & customer**, **Transaction**,
  **Receipt details** (and the equivalent for bulk). Every existing field keeps
  its `id`, value, placeholder, and hint.
- **Progressive disclosure:** primary sections `open` by default; advanced /
  low-frequency sections (bulk numbering suffixes, texture randomization, etc.)
  collapsed by default.
- Refine `.form-section` visuals: clearer summary rows with chevron affordance,
  consistent open/closed states, better body padding, consistent field spacing.
- Keep the existing "compact/comfortable/wide" panel-size two-column adaptive
  behavior working with the new section structure.

### 3. Generator context header + mode toggle

- A compact **sticky context header** at the top of the config panel: doc-type
  name/icon + the **Single / Bulk** segmented toggle, so context and mode are
  always visible while scrolling the form.
- Reuse existing mode-tab wiring (`data-mode` / mode-tab classes) — restyle only.

### 4. Generator responsiveness (secondary win)

- **≤900px: Edit ⇄ Preview segmented toggle.** Instead of the form and preview
  stacked in one long scroll, a segmented control switches the viewport between
  **Edit** (config panel) and **Preview** (preview area). Default to Edit; a
  persistent "Preview" affordance flips to the result. This uses a body/root
  state class toggled by JS; both panels remain in the DOM (no rewiring), only
  visibility switches.
- Add a **mid breakpoint (~1200px)** so the two-column form and panel sizing
  degrade gracefully between desktop and mobile.
- Tune resizable panel min/max and hide the resizer on touch widths (already
  hidden ≤900px — keep).
- Bulk mode already centers a single panel; ensure it flows correctly at all widths.

### 5. Marketing pages (about, features, 404)

- Tighten vertical rhythm and section spacing using the shared spacing scale.
- Improve hierarchy on `about` (steps) and `features` (detail grid): consistent
  card treatment, clearer headings, better mobile stacking.
- **Dedupe footer links** (features footer currently repeats "Fuel Receipt/
  Generator"). One clean, consistent footer link set across all pages.
- Polish `404` (already minimal) to match the refreshed card/spacing language.

### 6. Navigation clarity

- Header nav has 7 links — on desktop keep as-is but ensure the **active state**
  is obvious; on mobile the existing hamburger drawer stays, restyled for
  larger tap targets and clearer active item.

## Components / units of work

Each is independently reviewable and testable:

1. **Tokens & foundation** — shared spacing/shadow/focus tokens in `site.css` + generator `:root`.
2. **Marketing refinement** — about, features, 404, footer dedupe.
3. **Generator: fuel accordion conversion** — re-wrap fuel single/bulk into `<details>` sections (highest-care task; id preservation critical).
4. **Generator: section/visual restyle** — unified `.form-section`, context header, mode toggle styling, field spacing.
5. **Generator: responsive Edit⇄Preview** — CSS state + small JS toggle; mid breakpoint.
6. **Verification** — manual pass of all 5 doc types × single/bulk × export, at desktop/tablet/mobile widths, both themes.

## Testing / verification approach

Since this is a static site with no test suite, verification is manual and
behavioral, per doc type:
- Load each of `fuel-receipt`, `ecommerce-invoice`, `postpaid-bill`,
  `rent-receipt`, `driver-slip`.
- Toggle Single/Bulk; confirm all fields present, editable, and drive the preview.
- Run one export per type (PNG/PDF/ZIP) to confirm wiring intact.
- Check both themes and three widths (≈1440 / ≈768 / ≈390).
- Confirm no console errors and no missing-element JS breakage.

Served over HTTP (`python3 -m http.server`), not `file://` (required for
export/fonts).

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Breaking JS by touching generator markup | Never rename/remove `id`/`data-*`; re-wrap only. Diff-check ids before/after. |
| Fuel accordion conversion regresses adaptive two-column layout | Test compact/comfortable/wide after conversion. |
| Edit⇄Preview toggle hides an element JS expects to measure | Keep both panels in DOM; toggle visibility via CSS class, not removal. |
| Scope creep into output templates | Explicitly out of scope; only app chrome changes. |

## Rollout

Single working copy (no git repo initialized). Changes applied incrementally per
component with verification between generator tasks. If desired, `git init` can
be offered before starting so changes are checkpointed.
