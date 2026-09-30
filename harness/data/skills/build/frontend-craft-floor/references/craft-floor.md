# Pre-ship craft-floor checklist

Distilled from pbakaus/impeccable (Apache-2.0), `skill/reference/craft-floor.md` plus the 61
antipattern definitions in upstream's detector registry (`crates/foundation/src/registry.rs`).
Rule ids are kept in backticks so every line traces to upstream — nothing here is executable.

Work top to bottom, in ONE batched pass over the surface, then stop. Upstream ids marked
*advisory* are judgment calls, not defects; a hit is a question, never a blocker.

## 0. Functional first (never polish above a broken path)

Upstream triage order, highest band first:

1. Broken or blocked tasks, data loss, misleading state, inaccessible paths.
2. Missing states: loading, empty, error, success, disabled, permission.
3. Flow, hierarchy, responsive and design-system drift.
4. Visual and motion inconsistency.
5. Code and asset cleanup.

Do not perfect one corner while the rest of the surface sits below the same bar.

## 1. Does it work at all

- [ ] No `<img>` with empty/missing/placeholder `src` — they ship as broken-image boxes (`broken-image`).
- [ ] No uncaught script error on load; broken JS silently kills reveals and can leave most of a
      page invisible (`script-error`).
- [ ] Content is visible at rest — a large share of the text at `opacity: 0` /
      `visibility: hidden` after every reveal handler ran is the failed-reveal signature
      (`content-hidden-at-rest`). Visible by default, enhanced by JS.
- [ ] Nothing renders wider than its container, and nothing spills into a horizontal scrollbar
      (`text-overflow`).
- [ ] A clipping container (`overflow: hidden|clip`) is not cutting off tooltips, menus or popovers
      (`clipped-overflow-container`).
- [ ] No text painted under an opaque element or a second text run (`text-occlusion`).
- [ ] In a horizontal scroller or tab panel, cards keep an inset on **both** sides — flush-to-edge
      cards lose their corners (`edge-flush-cards`).
- [ ] In a multi-column opening section, no single column runs far past the fold while its sibling
      fits one viewport (`first-viewport-column-overflow`).

## 2. Legibility (numeric — read the computed values)

- [ ] Body and placeholder text ≥4.5:1; large text ≥3:1 (`low-contrast`).
- [ ] No gray text on a colored background (`gray-on-color`) — tint from the surface's hue.
- [ ] No pure black or pure gray; tint neutrals.
- [ ] Prose measure 65–75ch; lines over ~80ch are hard to track back from (`line-length`).
- [ ] Body line-height 1.5–1.7×; below 1.3× multi-line text is unreadable (`tight-leading`).
- [ ] Body text ≥14px (`tiny-text`); functional UI text — links, buttons, nav, labels, table cells,
      meta rows, timecodes — ≥11px, including inside footers. Being on the token ramp does not
      exempt a value: bumping the ramp launders the token, not the problem (`undersized-ui-text`).
- [ ] Heading levels never skip (`skipped-heading`).
- [ ] Space above a heading exceeds space below it (`heading-rhythm`).
- [ ] No all-caps body copy (`all-caps-body`); wide tracking reserved for short uppercase labels,
      never body text (`wide-tracking`).
- [ ] Letter-spacing no tighter than -0.04em (`extreme-negative-tracking`).
- [ ] The same literal text is not rendered 3+ times in one card or panel (`repeated-container-text`).
- [ ] Body paragraphs are not flush against the viewport edge — ≥16px, ideally 24–32px, of
      horizontal padding, or a max-width wrapper (`body-text-viewport-edge`).
- [ ] Text inside bordered, outlined or colored containers is inset ≥8px, ideally 12–16px
      (`cramped-padding`).

## 3. Hierarchy and rhythm

- [ ] Adjacent type roles step by ≈1.2× or more; a flat ramp fails at every step
      (`flat-type-hierarchy`).
- [ ] A long sentence is not set at display size; a punchy one- or two-word headline at that size
      is fine (`oversized-h1`).
- [ ] Spacing is not one value repeated everywhere — tight inside groups, generous between them,
      and more space above a heading than below (`monotonous-spacing`).

## 4. The slop tells (the highest-value bans)

- [ ] No purple/violet gradient, no cyan-on-dark ground (`ai-color-palette`). Cream/beige page
      backgrounds need a real reason (`cream-palette`).
- [ ] No gradient text — emphasis comes from weight or size (`gradient-text`).
- [ ] No card inside a card (`nested-cards`).
- [ ] No thick colored side border (the single most recognizable AI tell) and no thick accent border
      fighting a rounded corner (`side-tab`, `border-accent-on-rounded`).
- [ ] No small rounded-square icon tile above a heading; put the icon beside the heading or in flow
      (`icon-tile-stack`).
- [ ] No kicker/eyebrow label above a heading — delete it and let the heading speak
      (`kicker-above-heading`). A pill-chip eyebrow on an oversized hero is the same tell
      (`hero-eyebrow-chip`). *advisory*: `01/02/03` section numbers (`numbered-section-labels`).
- [ ] No uniform card grid of icon + heading + text as the page structure, and no big-number hero
      metric template.
- [ ] No Inter (or Roboto, Geist, Plus Jakarta Sans, Space Grotesk, Fraunces) by default
      (`overused-font`) — or, in Operate, one well-tuned sans used deliberately across every role.
- [ ] Not both a hairline border and a wide diffuse shadow; commit to one (`gpt-thin-border-wide-shadow`).
- [ ] No zero-offset colored glow halo, and no colored blurred shadow on dark
      (`dark-glow`, `radial-halo`, `radial-spotlight-glow`) — a chromatic wash behind a hero is the
      same tell drawn with a gradient.
- [ ] No repeating-gradient stripes or hairline grid background unless a real canvas, map, blueprint
      or measuring surface sits under them *(`repeating-stripes-gradient`, `codex-grid-background`)*.
- [ ] Nothing decorative is invisible: no raster buried under a ≥0.9-alpha wash or at near-zero
      opacity (`buried-raster`); organic contours cut with a many-vertex `clip-path` read as cheap
      (`organic-clip-path`).
- [ ] Icons are drawn in one consistent stroke and weight, not emoji or unicode glyphs. A
      hero-sized pictorial SVG assembled from primitives reads as clip art
      *(`shape-assembled-illustration`)*.
- [ ] Light or dark chosen from the use scene — who, where, under what ambient light — not by
      category habit.

## 5. Motion

- [ ] `prefers-reduced-motion` is honored, and the reduced branch still conveys the state change and
      the hierarchy. A blanket `0.01ms` kill switch that destroys useful feedback is a defect, not a
      fix.
- [ ] No bounce or elastic easing — real objects decelerate; use exponential ease-out
      (`bounce-easing`).
- [ ] No auto-scrolling marquee (`marquee`) and no page-load choreography in Operate.
- [ ] A pulsing dot appears only on genuinely live, changing data (`pulsing-dot`); a blinking cursor
      appears only where a real input is *(`blinking-cursor`)*.
- [ ] One authored moment, not the same entrance on every section.
- [ ] Images do not scale or rotate on hover, directly or through a parent — an image is not an
      action target; give the container the feedback *(`image-hover-transform`)*.

## 6. Copy and content

- [ ] Controls name their action; errors name the problem and the recovery.
- [ ] No generic SaaS buzzwords — streamline, empower, supercharge, world-class, enterprise-grade,
      next-generation, cutting-edge (`marketing-buzzword`); no dismissing framing copy
      (`theater-slop-phrase`).
- [ ] Not 3+ sections each landing on a short rebuttal sentence (`aphoristic-cadence`).
- [ ] Em-dash density near one per 500 characters of body copy, or ≥8 total, is saturation
      *(`em-dash-overuse`)*.
- [ ] Every brief requirement is present and findable within seconds.
- [ ] Claims and configuration come from supplied truth; illustrative values are labeled honestly.

## 7. Browser surfaces (the cheapest "built, not assembled" signal)

The parts you did not draw still carry the design. Theme from the palette:

- [ ] Text selection, the caret, focus rings, underline offset, scrollbars.
- [ ] Tabular numerals in data tables.

## 8. Fit the mode (do not ship one register everywhere)

- **Persuade** — design is the product; real imagery where the brief needs it; earn the action.
- **Operate** — one font family is often right; a fixed rem scale, not fluid clamp; tighter scale
  ratio; a standardized state vocabulary (hover, focus, active, disabled, selected, loading, error,
  warning, success, info); accent for primary action, selection and state only; no display fonts in
  UI labels, buttons or data; no reinvented standard affordances; no modal as the first thought.
- **Read** — comprehension first; prose measure and navigation outrank component density.
- **Experience** — the artifact leads from the first viewport; the interface recedes.

## 9. Boundaries of this checklist

- Upstream runs these as a Rust detector (`npx impeccable detect`), an edit hook and a live browser
  overlay. **None of that exists in this harness.** Never claim a detector run, a render, a
  screenshot or a browser check. You did not perform one.
- This is a build-time UI check. It does **not** supersede `review/code-review-and-quality`,
  `review/code-simplification` or `review/ponytail-review`.
- A clean pass here is evidence, not proof: it never replaces inspecting the real interaction path,
  and it never justifies polishing outside the scope of the task.
