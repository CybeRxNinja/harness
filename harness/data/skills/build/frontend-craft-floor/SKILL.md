---
name: frontend-craft-floor
description: Craft floor for UI edits: 4 modes, refuse list, pre-ship checks. Use before shipping any UI change.
license: Apache-2.0
source: https://github.com/pbakaus/impeccable
adapted-from: pbakaus/impeccable (skill/SKILL.src.md + skill/reference/{craft-floor,operate}.md)
---
# Frontend Craft Floor
Doctrine adapted from pbakaus/impeccable (Apache-2.0): craft floor, refuse list, 4 Modes, and
the detector rules flattened into human checks.

## When to Use
Any change a person will look at — markup, CSS, Tailwind, component props, UI copy. Load
immediately before editing, including a one-line refinement. Not for backend-only work.

## Boundaries
- Upstream's detector engine, its 61 rules as executable checks, the font index, the edit hooks
  and the live/browser overlay are **not available here**. Never claim a detector run, a render,
  a screenshot or any visual check. You did not perform one; name every skipped check as skipped.
- Build-time UI craft. It does **not** supersede `review/code-review-and-quality`,
  `review/code-simplification` or `review/ponytail-review` — those are the merge gate.

## Procedure
1. **Pick the Mode from the surface being touched, not the product.**

   | Mode | The visitor… | Surfaces | Bias |
   |---|---|---|---|
   | Persuade | decides and acts | landing, marketing, pricing | design *is* the product; earn attention, then the action |
   | Operate | completes a task | app UI, dashboards, editors, admin, settings | scanability and the real usage scene outrank expression |
   | Read | understands | docs, articles, guides, help, changelogs | comprehension first; measure and navigation over density |
   | Experience | is inside the work | portfolios, galleries, showcases | the artifact leads; the interface recedes |

   A devtool's landing page is still Persuade; fashion-house docs are still Read; a docs index is
   Read, not Persuade. Persist the mode for that surface only.
2. **Clear the craft floor** on the built result, not on the intention.
3. **Refuse the defaults** below, or name the pinned brief that earns the one you kept.
4. **Run the pre-ship checklist** (`references/craft-floor.md`, via `skill_view`) immediately
   before shipping, top to bottom, in one batched pass.

## Craft floor
- **Type scale & rhythm.** A fixed ramp with visibly different steps (≈1.2× or more), body measure
  65–75ch, body ≥14px, functional UI text ≥11px, tracking no tighter than -0.04em. Run the real
  copy at every breakpoint; fix what overflows.
- **Spacing rhythm.** Tight inside a group, generous between groups, more space above a heading
  than below it. One value repeated everywhere is not rhythm. Read the computed values.
- **Real hierarchy.** The surface says where to look first, without a border on every box.
- **One accent, used sparingly.** It marks primary action, current selection and state — never
  decoration. Operate defaults to restrained; committed is an earned exception.
- **No decoration-as-decoration.** A gradient, glow, blur, stripe or grid earns its place by
  carrying meaning. Shadows carry an offset and a soft blur.
- **Contrast.** Body and placeholder text ≥4.5:1, large ≥3:1. On a colored surface tint secondary
  text from that hue — never gray, never pure black.
- **Motion respects `prefers-reduced-motion`.** The reduced branch keeps the state change and the
  hierarchy; a blanket `0.01ms` kill switch that deletes useful feedback is a defect. Motion
  conveys state; one authored moment beats the same entrance on every section.
- **States and access.** hover, focus-visible, disabled, loading, error, empty. Keyboard reachable
  with a visible focus ring; no keyboard traps.

## Refuse list
Category defaults, not bans — a pinned brief can earn one. Reaching for one on a free axis means
you were not deciding; recognising that means rewriting the element, not softening it.

- **Inter-everywhere** — also Roboto, Geist, Plus Jakarta Sans, Space Grotesk, Fraunces. Pick a
  face whose character fits, or one well-tuned sans where the mode is Operate.
- **Purple→blue gradients**, gradient text, cyan-on-dark. Emphasis comes from weight or size.
- **Card-inside-card.** Cards are the lazy container; nesting is always wrong. Flatten with spacing,
  type and dividers.
- **Gray on color.** Tint from the surface's own hue.
- **An icon tile above every heading.** Also the same kicker/eyebrow above every heading, `01/02/03`
  section numbers, the uniform card grid of icon + heading + text, the big-number hero metric.
- **Uniform spacing** that reads as template output.
- Also refused (`references/craft-floor.md` §4–6): side-tab accent borders, marquees, bounce
  easing, pulsing dots on static data, all-caps body, sparklines as content.

**Fix the cause, not the symptom.** Classify the drift first — missing token, one-off that should
be a shared component, conceptual mismatch, local defect — and fix it at the narrowest correct
level. **Never repair unrelated drift as a side effect of this task:** report it, don't smuggle it
into the diff.

## Pitfalls
- Announcing the checklist instead of applying it.
- Restyling as a side effect of an unrelated change. Scope discipline outranks taste.
- Reading the floor as a redesign licence: refinement preserves identity, behavior and copy;
  replacing the visual world is a separate, asked-for decision.
- Reading the floor as a ceiling. It holds mechanics, it never picks the direction.
- Polishing one corner while the rest sits below the same bar, or open-ended self-QA. One
  batched round, fix what it shows, confirm once, stop.

## Verification
- The Mode and a one-line reason for it appear in the reply.
- The floor was walked against the built result — computed values where a check is numeric — and
  `references/craft-floor.md` was run top to bottom in one pass.
- Every finding cites what was inspected (`file:line`); every unrun check is named as unrun; no
  detector, render or browser claim is made.
