# Anteroom design rules

How Anteroom's web UI should look, and what to check before shipping a visible
change. These rules keep the app consistent with its best screens (Settings,
the chat composer) and away from the look of generated UI. They are drawn from
Linear's and Vercel's published design guidance and Stripe's work on color
systems (sources at the end).

When a rule here and a screen in the app disagree, the rule wins: fix the
screen in the same PR or note it for a follow-up.

## Principles

1. **Neutral by default.** Text, borders and surfaces are gray. Color means
   something: the primary action, a link, focus, or a status the seller should
   notice. A marketplace's brand color appears in its logo only.
2. **One of everything.** One page layout, one tab style, one card surface, one
   type scale. Before writing new markup, find the shared component that
   already does it.
3. **Content first, chrome quiet.** Navigation, labels and helper text sit
   behind the seller's data. Nothing decorative competes with a listing, a
   price or an order.
4. **Say less.** A page description is one sentence. Explain a rule once, where
   the seller makes the decision, not in every section.

## Use these, not new markup

| Need | Use |
|---|---|
| Full-width page | `WorkspacePage` (`src/components/WorkspacePage.tsx`) |
| Page title, one-line description, actions on the right | `PageHeader` |
| Section with a title and optional actions | `PageSection` |
| Grouped surface with hairline dividers | `Panel`, `RowList`, `ListRow` |
| Labelled form control | `FormField` (label above, 12px medium, muted) |
| Empty list | `EmptyRow`, or `Empty` from `ui/empty` for a whole page |
| Loading page | `PageSkeleton` (never a bare "Loading…" line for a page) |
| Errors | `PageError`, `FormError` |
| Page-level tabs | `WorkspaceTabs` with `main` |
| A choice between two to four views inside a panel | `ToggleGroup` (segmented) or `WorkspaceTabs` without `main` |
| Buttons, inputs, selects, checkboxes, switches, badges | `src/components/ui/*` |
| Settings-style rows | the components in `components/settings/` |

Do not use a raw `<select>` or `<input type="checkbox">` in new code. Use
`Select`, `Checkbox` and `RadioGroup`. The one exception is `NativeSelect` in
`ListingFormControls.tsx`: marketplace form fields with long option lists use it
so phones show their own picker and keyboard. Use it only for that.

## Tokens

Use the theme variables and Tailwind classes from `styles/t3-theme.css`. Never
hard-code a hex or `rgb()` color in a component or legacy stylesheet. The one
exception is drawing over live third-party content (the browser preview).

**Text color**
- `text-foreground`: primary text and figures.
- `text-muted-foreground`: secondary text, labels and descriptions.
- Status: `text-success-foreground`, `text-warning-foreground`,
  `text-destructive-foreground`, `text-info-foreground`. Use them only for a
  state that needs attention. The default state (for example *Draft*) stays
  muted.

**Surfaces**
- Page: `bg-background`.
- Card or panel: `rounded-xl border border-border/60 bg-card/40`. In dark mode
  depth comes from a slightly lighter surface and a soft border, not from
  shadows or glows.
- Hover and selected rows: `bg-accent`. Do not mark selection with a colored
  stripe on one edge.

**Type scale.** Pick from this list; do not invent sizes.

| Use | Size | Weight |
|---|---|---|
| Page title (`PageHeader`) | 20px (`text-xl`) | 600, `tracking-tight` |
| Large figure in a metric card | 24px | 600, `-0.02em` |
| Dialog title, phone page title | 16–17px | 600 |
| Section title, row title, body | 14px (`text-sm`) | 500 for titles, 400 for body |
| Labels, descriptions, metadata | 12px (`text-xs`) | 500 for labels, 400 otherwise |
| Dense metadata, IDs, counters | 11px (`text-2xs`) | 400 |

- Weights are 400, 500 and 600 only. No 700 or 800 in app UI.
- No uppercase or letter-spaced "eyebrow" labels above headings. Labels are
  sentence case.
- Figures, prices and counts use `tabular-nums`. Order IDs, SKUs and other
  machine values use `font-mono` at `text-2xs`, muted.
- Tighten letter-spacing only at 20px and above.

**Spacing.** Multiples of 4px: 4, 8, 12, 16, 20, 24, 32, 48. Inside a panel, 16px
padding (12px on phones). Between page sections, 24–32px.

**Radius.** Controls `rounded-lg` (10px); cards and panels `rounded-xl` (14px);
dialogs as `ui/dialog` sets them. An inner radius is never larger than its
container's.

## Patterns

**Page header.** Title, at most one sentence of description, actions on the
right with at most one primary button. The breadcrumb already names the page;
do not repeat it in a second large heading below. If a page has no visible
title, keep an `sr-only` `<h1>`.

**Tabs.** Page tabs are muted labels with a 2px foreground underline on the
active one (`.ebay-navigation`). No brand-colored or tinted tabs, no bordered
buttons pretending to be tabs. One tab style per level of navigation across
the whole app.

**Metrics.** Label (12px, muted), figure (24px), optional one-line footnote.
An unknown value keeps its words ("Not available", "—") but renders at body
size in muted color (`MetricValue`), so it never looks like data. Never show
`$0.00` for something that was not recorded.

**Lists.** One `Panel` with dividers, not a stack of separate cards. Title on
the left, the figure right-aligned, actions at the end of the row. Do not nest
cards inside cards.

**Forms.** Label above every control. Group related checkboxes under a
`legend` ("Actions", "Marketplaces"). Placeholders show an example or the
empty meaning ("Any", "All matching items"), never a value that looks
filled in. Show proper names (`marketplaceName("ebay")` is "eBay"), never
internal ids. Put an error next to its field.

**Loading and empty states.** Skeletons should match the shape of the content
they replace. An empty state says what is missing and what to do next, in one
line.

## What generated UI tends to get wrong

Reject a change that adds any of these:

- Purple, indigo or blue gradients; glows; aurora or blurred color backgrounds.
- Brand or accent colors in chrome: tab underlines, card top borders, side
  stripes, tinted section backgrounds.
- Uppercase, letter-spaced labels above every heading.
- Oversized bold headings (28px+, weight 700+) inside the app.
- A heading, a subheading and a paragraph that all say the same thing.
- Every section boxed in its own bordered card; cards inside cards.
- Emoji as icons. Use `lucide-react` icons at 16px, muted unless active.
- Native browser controls next to styled ones.
- A new gray, radius or font size that isn't in the scales above.
- Helper paragraphs longer than two lines.

## Before you ship a visible change

`scripts/check-design.py` (run by `./check.sh` and CI) fails on a new hard-coded
color, uppercase label, weight above 600, gradient, native select or checkbox,
or arbitrary text size. `design_baseline.json` records the cases that were
already there; it only shrinks. The rest of this list needs a person's eye.


- [ ] Uses the shared components above; no one-off page, tab or card styles.
- [ ] No hard-coded colors; status color only where the state needs attention.
- [ ] Sizes, weights, spacing and radii come from the scales above.
- [ ] Figures use `tabular-nums`; IDs are mono and muted.
- [ ] Empty, loading and error states are designed, not left blank.
- [ ] Copy is short and in the seller's words; no repeated explanations.
- [ ] Checked in the sandbox in dark and light, at desktop (1440×900) and phone
      (iPhone 15) sizes, with screenshots in the PR.

## Sources

- Linear, [How we redesigned the Linear UI](https://linear.app/now/how-we-redesigned-the-linear-ui)
  and [Behind the latest design refresh](https://linear.app/now/behind-the-latest-design-refresh)
- Vercel, [Web Interface Guidelines](https://vercel.com/design/guidelines),
  [Geist colors](https://vercel.com/geist/colors) and
  [Geist typography](https://vercel.com/geist/typography)
- Stripe, [Designing accessible color systems](https://stripe.com/blog/accessible-color-systems)
- The Fountain Institute, [Signs of a vibe-coded UI](https://www.thefountaininstitute.com/blog/signs-vibe-coded-ui)
