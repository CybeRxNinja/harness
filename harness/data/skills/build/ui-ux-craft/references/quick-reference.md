# UI/UX quick reference

Concrete values for the domains in `SKILL.md`. Load on demand, one section per row you are fixing.
Everything here is a **built-in default**, not a retrieved result — if a value below does not match the
project's own design system, the project wins. Do not cite a number that is not on this page.

## 1. Accessibility
- Contrast: 4.5:1 body text, 3:1 for ≥24px or ≥19px bold, 3:1 for UI borders and focus rings.
  Non-text (icons, input borders, chart marks) ≥3:1 against their background.
- Focus: never remove the outline without a replacement that is at least as visible. Use
  `:focus-visible` so pointer users do not get rings on click.
- Target size: 44×44px comfortable, 24×24px minimum for inline/dense controls; ≥8px between adjacent
  targets. Icon-only buttons carry `aria-label`.
- Keyboard: every interactive element reachable in DOM order; modals trap focus, close on `Esc`, and
  return focus to the trigger. Never `tabindex` > 0.
- Motion: honour `prefers-reduced-motion: reduce` — drop transforms and parallax, keep opacity fades.
- Semantics: real `button`/`a`/`input`; `alt=""` for decorative images, descriptive alt for
  informative ones; one `h1`, no skipped heading levels; `<label for>` on every field.
- Live regions for async results: `role="status"` (polite) or `role="alert"` (assertive) — not a
  re-rendered container.

## 2. Color & contrast
- Semantic tokens only: `bg`, `surface`, `border`, `text`, `text-muted`, `accent`, `danger`,
  `success`, `warning`. Components consume tokens, never raw hex.
- Accent is reserved: one interactive accent, plus status colors that never double as accents.
- Muted text still passes 4.5:1 — "muted" is a token, not an excuse to drop below the ratio.
- Dark mode is a second token set, not an inverted one: recheck every pair, especially borders and
  shadows (shadows read as nothing on dark backgrounds).
- Never encode state in hue alone — pair with icon, text, weight, or shape.
- Categorical series: cap at ~6–8 distinguishable hues, cycle with pattern/marker changes rather than
  adding hues.

## 3. Layout & spacing
- Spacing scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96. Pick the density deliberately —
  dashboards 8–32, marketing 24–96. No off-scale values.
- Breakpoints: mobile-first, ~480 / 768 / 1024 / 1280. Add a breakpoint only when content breaks.
- Containers are `max-width` + fluid, never fixed `width` in px. `width: 100%` plus `max-width`.
- `100vw` includes the scrollbar and overflows — use `100%`.
- No horizontal scroll at 320px or at 200% browser zoom; nothing important clipped.
- Text measure 45–75ch for prose. Full-width body text is a readability bug.
- Grid: consistent gap, `align-items` explicit, `minmax(0, 1fr)` for grid children that hold long
  content (otherwise they blow out the track).
- Reserve space for async content (skeleton at final size) — Cumulative Layout Shift < 0.1.

## 4. Component architecture
- Variants over forks: one component with `variant`/`size` props before a second component.
- Composition over configuration: slots/children beat a `props` object that grows forever.
- Tokens are the shared value; a value repeated in three files belongs in one place.
- Colocate styles with the component; no global selector reaching into another component's DOM.
- Keep the primitive dumb (markup + tokens) and the logic in the caller.
- If a component has one user, do not build a design system around it yet.

## 5. Typography
- Base 16px; never below 12px for body, 11px absolute floor for legal/meta text only.
- Line height ~1.5 body, 1.1–1.25 headings; ~1.4 for long-form.
- Type scale: 1.25 (major third) or 1.2; pick one and use it. Weight carries hierarchy, not size alone.
- Headings: semantic rank first, then size — screen readers navigate by level.
- Tabular numerals (`font-variant-numeric: tabular-nums`) in tables and prices so columns align.
- Truncation: one line + ellipsis + `title`/tooltip is acceptable for table cells; never for the
  primary label of a control. Long tokens (URLs, emails, IDs) must wrap or break, not overflow.
- Load no more than 2 families; pair on contrast of role (display vs text), not decoration.

## 6. Forms & feedback
- Label always visible and above the field. Placeholder is a format example, never the label.
- Validate on blur for the first error, then live after the field is dirty. Never while typing the
  first characters.
- Error text sits next to its field, names the problem, and says what to do. Mark the field
  `aria-invalid` and link with `aria-describedby`.
- Error summary at the top is an addition for long forms, not a replacement for inline errors.
- Every submit has pending (spinner + disabled + label change) and success (confirmation + what
  happens next) states. Never leave the user guessing.
- Destructive actions: confirm with the consequence named, not "are you sure?".
- Progressive disclosure: show the required fields first, advanced options behind an explicit control.
- Preserve input on validation failure. Losing a filled form is a data-loss bug.

## 7. Motion
- Durations: 100–150ms micro (hover, toggle), 200–300ms enter/exit, ~300–500ms large transitions.
  Exit is faster than enter.
- Easing: ease-out for entering, ease-in for exiting, ease-in-out for moving. One linear animation
  reads as a bug.
- Animate `transform` and `opacity` only — they skip layout and paint. Never `width`, `height`, `top`.
- Motion must convey meaning: where a thing came from, that state changed, what is now interactive.
  Purely decorative loops get removed.
- Stagger ≤50ms per item, and cap the total (≤10 items or ~400ms, whichever first).
- Under `prefers-reduced-motion: reduce`, remove transforms/parallax/auto-play; keep short fades.
- Never animate on top of a loading state or block input while animating.

## 8. Imagery & icons
- One icon family, one stroke weight, one grid size (16/20/24). Mixing families reads as unfinished.
- Icons scale with text (`1em`) or the size scale — not arbitrary pixels.
- Decorative icon next to a text label: `aria-hidden="true"`. Icon-only: `aria-label` + `title` tooltip.
- SVGs inline for color/stroke theming; use `currentColor`, never a hardcoded fill in a component.
- Images: explicit `width`/`height` (or aspect-ratio) to reserve space, `loading="lazy"` below the
  fold, `decoding="async"`, WebP/AVIF with a fallback, meaningful `alt`.
- Never use emoji as UI icons; never use an icon where a text label is clearer.
- Icons do not replace labels on primary actions.

## 9. Microcopy
- Buttons: verb + object, outcome-first — "Save changes", "Invite member", "Delete project". Never
  "Submit", "OK", "Yes".
- Errors: what happened + what to do. "Payment failed. Your card was declined — try another card."
- Empty states: name the content that is missing and give the one action that fills it.
- Confirmations: say what happened and what is reversible, if it is.
- Title case for buttons and headings, sentence case for body, helper text, and long copy.
- Match the user's vocabulary, not the database's column names. No blame ("invalid input" → "that
  email doesn't look right").
- Numbers, dates, and currency: one format per surface; always with units.

## 10. Charts & data-viz
- Pick by question: comparison → bar; trend → line; distribution → histogram/box; part-of-whole with
  ≤3 slices → stacked bar; correlation → scatter. Pie beyond ~4 slices is unreadable.
- Label axes with units. Start value-truncated bars at zero; lines may not.
- Legend present whenever ≥2 series, and direct-label the series when there is room.
- Tooltip on hover/focus with the real value; keyboard-focusable marks for the same data.
- Never color alone: add pattern, marker shape, or direct labels for color-blind readers.
- Muted gridlines, no chartjunk, no 3D, no dual y-axis.
- Missing data is shown as a gap or an explicit "no data" — never interpolated silently.
- Provide the numbers in text nearby when the chart carries a decision.

## 11. Stack notes (only for the detected stack)
- **React/Next:** keys by stable id, memoize lists only after measuring, avoid `useEffect` for derived
  state, `next/image` for content images.
- **Vue/Svelte/Astro:** keep state local, no prop drilling past two levels — use a store/composable.
- **Tailwind:** arbitrary values defeat the scale; extend the theme instead. Group with `@layer`.
- **Flutter:** use theme colors, `Semantics` widgets for a11y, `const` constructors, avoid `setState`
  in `build`.
- **SwiftUI:** dynamic Type not fixed sizes, `.accessibilityLabel`, `@ScaledMetric` for spacing,
  avoid hardcoded `.frame` widths.
- **React Native:** `Pressable` over `TouchableOpacity`, safe-area insets, `accessibilityRole` +
  `accessibilityLabel`, 44pt minimum touch target.
