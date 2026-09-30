---
name: ui-ux-craft
description: Design-time UI/UX rules by priority: a11y, color, type, layout, motion, forms, charts. Use when building interfaces.
license: MIT
source: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
adapted-from: nextlevelbuilder/ui-ux-pro-max-skill (SKILL.md + references/quick-reference.md)
---
# UI/UX Craft
Design-time doctrine, ordered by blast radius. Concrete values live in
`references/quick-reference.md` — load it when a row below bites, never preemptively.

## When to Use
Writing or fixing UI where how it looks, moves, and responds to input is the deliverable: new pages,
components, design systems, visual polish, or a UI pass on existing markup. Not backend logic, infra,
or non-visual performance.

Not a merge gate. For pre-merge review load `review/code-review-and-quality` instead — that one owns
the five-axis verdict; this one owns design-time decisions, and its steps do not restate that
procedure. Accessibility here is **craft, not threat modeling**: contrast, focus, target size, reduced
motion, semantic markup. It is deliberately kept apart from `review/security-and-hardening` (a11y ≠
OWASP; that skill owns inputs/auth/storage/integrations). Never copy OWASP rules in here, and never
claim a UI change is "secure" because it is accessible.

## Procedure
1. **Detect the stack, never assume it.** Read the real manifest first: `package.json`
   (react/next/vue/svelte/nuxt/angular), `pubspec.yaml` (flutter), `*.xcodeproj` or `Package.swift`
   (swiftui), `composer.json` (laravel), `app.json` + `react-native` dep (native RN). No manifest
   found and stack guidance matters → ask. A hardcoded default silently misroutes every
   recommendation after it, and the error surfaces as "why doesn't this read like our app".
2. **Name the frame** from the request: product type, who uses it and where, platform, style intent.
3. **Walk the table top-down.** Fix every Critical row before starting a High one; skip rows the
   code already satisfies. State which rows you checked.
4. **Pull values per row** from `references/quick-reference.md` for the rows you are touching.
5. **0-result honesty.** No search index backs this skill, so every rule here is a built-in default.
   Two rules follow: (a) never name a rule, ratio, or token you cannot point at in
   `references/quick-reference.md` — if it is not written down, call it judgement; (b) when a lookup
   against the project (an existing token, a platform spec) returns nothing usable, say it returned
   nothing and name the default you fell back to. Zero results dressed as data is the one failure this
   rule exists to stop.

## Domains by priority
| # | Domain | Sev | Must-have check | Anti-pattern |
|---|--------|-----|-----------------|--------------|
| 1 | Accessibility | CRITICAL | Text contrast ≥4.5:1 (≥3:1 large), visible focus ring, keyboard reaches every control, labelled icon-only buttons, semantic elements | `outline: none` with no replacement, div-as-button, color as the only signal |
| 2 | Color & contrast | CRITICAL | Semantic tokens (not raw hex in components), one accent with reserved meaning, light+dark pairs checked | Gray-on-gray, token drift between screens, accent used for everything |
| 3 | Layout & spacing | HIGH | A spacing scale, mobile-first breakpoints, no fixed-px containers, no horizontal scroll at 320px | Magic numbers, desktop-first fixed widths, `100vw` causing overflow |
| 4 | Component architecture | HIGH | Shared primitives, props over forked copies, one source for the shared value | Two near-identical buttons, per-screen CSS overrides, premature abstraction |
| 5 | Typography | HIGH | ≥16px base, ~1.5 line height, max ~65–75ch measure, real heading order | Body text <12px, jumping scale, headings chosen for size not rank |
| 6 | Forms & feedback | HIGH | Persistent visible label, error next to its field, helper text, focus moves on error, submit shows progress | Placeholder-as-label, errors only at the top, no disabled/pending state |
| 7 | Motion | MEDIUM | Motion carries meaning, duration matches distance, `prefers-reduced-motion` honoured | One duration for everything, animating width/height, motion that delays input |
| 8 | Imagery & icons | MEDIUM | One icon set, consistent stroke weight, decorative art `alt=""`, text alternative for meaning | Emoji as icons, mixing icon families, missing alt on informative images |
| 9 | Microcopy | MEDIUM | Button names are verbs with outcomes, errors say what to do next, empty states offer the next action | "Submit"/"OK" alone, error text naming the exception, empty state as a shrug |
| 10 | Charts & data-viz | LOW | Labelled axes, legend, tooltip on hover, never color-alone | Pie of 9 slices, unlabelled axes, rainbow categorical palettes |

## Pitfalls
- "I'll do accessibility at the end" → rejected: it is row 1, and retrofitting focus management and
  semantics rewrites the markup you just wrote.
- Recommending a framework feature for a stack you assumed → the single highest-cost error here.
  Re-read the manifest and redo the recommendation.
- Stating a number you cannot cite ("use 4.8:1 like the guide says") → either it is in
  `references/quick-reference.md` or it is a default; label it.
- Loading this at merge time and re-reviewing the diff → `review/code-review-and-quality` owns that.
- Treating this as a security pass, or pulling OWASP input-validation rules into a UI review.
- Restyling what works. The ladder: does this change need to exist? The smallest change in the wrong
  place is a second bug.

## Verification
Before delivering, state in the reply: the detected stack and the file that proved it; which Critical
and High rows were checked and how (contrast ratio computed, focus ring present, target size, keyboard
path); which rules came from `references/quick-reference.md` and which were labelled judgement or
default. Any unverified row is named as unverified, not passed.
