# Anteroom design rules

How Anteroom's web UI should look, and what to check before shipping a visible
change. These rules keep the app consistent with its best screens (Settings,
the chat composer) and away from the look of generated UI. They are drawn from
Linear's and Vercel's published design guidance and Stripe's work on color
systems (sources at the end).

When a rule here and a screen in the app disagree, the rule wins: fix the
screen in the same PR or note it for a follow-up.

## Principles

1. **Neutral by default.** Text, borders, surfaces and the primary action are
   gray or ink. Color means a status the seller should notice. The primary
   button is the darkest thing on the page (light) or the lightest (dark), as
   in Linear and Vercel; it is never a brand blue. A marketplace's brand color
   appears in its logo only.
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
| Workspace page | `WorkspacePage` (`src/components/WorkspacePage.tsx`) |
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
  state that needs attention. A routine state (*Draft*, *Active*, *Sold*) is a
  muted word with a 6px colored dot (`ListingStatusLabel`), not colored text.
- Primary action and focus: `bg-primary` is ink (zinc-900 / neutral-100) and
  the focus ring is gray. Do not reintroduce a blue accent for buttons,
  switches, charts or selection.

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
| Listing row title in the sidebar | 16px (`text-base`) | 500 |
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

**Page width.** Every page uses one of two widths from `PAGE_WIDTH` in
`WorkspacePage.tsx`. `readable` (896px, the default) is for forms, settings
and lists read top to bottom: Sourcing, Settings, Automations. `expanded`
(1280px) is only for pages built around a table, calendar or chart:
Marketplaces, Analytics. Do not add a third width or a page-local `max-w-*`.

**Navigation.** Every page link has a visible name or, where space forces an
icon, a tooltip with its name. The sidebar footer shows names once the sidebar
is wider than 18rem and falls back to icons below that.

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

## How the interface behaves

The rules above cover how Anteroom looks. These cover how it responds. They
follow Nielsen's usability heuristics, NN/g's research on response times and
progress, Material 3, Apple's HIG, Polaris and WCAG 2.2 (sources at the end),
and the responsiveness targets in `AGENTS.md`.

**Acknowledge every input at once.** A tap, click or keystroke changes
something on screen within 100ms: the button presses, the row shows its new
state, the text appears. Save edits optimistically and show "Saving…" quietly
only when it takes longer than a second. Never freeze the page while the
server answers.

**Always show what is happening.** Any wait longer than a second gets a
status in place, on the thing it is about: the marketplace row says
"Listing…", not a spinner in a corner. Under 10 seconds, an indeterminate
spinner and the step's name are enough. Longer work shows the current step
("Uploading photos", "Saving the listing") or a count ("3 of 8"). Use a
determinate progress bar only when the number is real.

**Busy is a state, not an error.** When something the seller asked for has to
wait its turn (a marketplace already working on another item, a provider at
its limit), accept the request, show it as *Waiting* with the reason ("Waiting
for Depop · 1 ahead") and a **Cancel**, and start it when its turn comes. Do
not refuse the click with a red message that tells the seller to try again
later. Waiting work lives only as long as the page that started it unless the
seller asked for it to be kept, and a marketplace action still runs only what
the seller confirmed (see **Security and Product Invariants** in `AGENTS.md`).

**Keep the seller moving.** Long work never blocks navigation, typing or
another draft. Show running and waiting work in one place the seller can
open from anywhere (a small activity tray, like a browser's downloads list)
as well as on the item itself, and send the seller to the item when it needs
them.

**Prevent errors before reporting them.** Disable or hide an action that
cannot succeed, and say why next to it ("Add a price to list on Mercari").
Validate a field when the seller leaves it, not on every keystroke. Keep what
the seller typed when something fails.

**Write errors the seller can act on.** Say what failed, in the seller's
words, and what to do next: "Depop didn't accept the size. Pick one from
Depop's list." Name the marketplace or provider that failed. No codes, stack
traces or "Something went wrong" on their own. Put the error next to the
thing it is about; use a page-level error only when the whole page failed.

**Undo over confirm, except where it reaches outside.** Changes inside
Anteroom (archive, delete a draft, remove a photo) happen at once and offer
**Undo** in a toast. Ask first only when the action cannot be undone or
reaches a marketplace, buyer or account: publishing, delisting, ending an
eBay listing, sending an offer. Those confirmations name what will happen and
where ("List on eBay and Depop"), and the button says the action, never "OK".

**Toasts are for news, not for decisions.** A toast reports something that
just happened somewhere the seller isn't looking, stays for about five
seconds (longer if it has an action) and holds at most one action. Anything
the seller must decide or read carefully goes in place, not in a toast.

**Remember where the seller was.** Going back restores the list's scroll
position, filters, search and selection. A half-written message or edit
survives switching items.

**Reachable by everyone.** Every action works from the keyboard with a
visible focus ring. Touch targets are at least 24×24px (WCAG 2.2 AA) and 44px
on phones (HIG). Status changes that appear without a page change (*Listed*,
*Waiting*, an error) are announced to screen readers through a live region.
Never rely on color alone: a status has a word or an icon too.

## What generated UI tends to get wrong

Reject a change that adds any of these:

- Purple, indigo or blue gradients; glows; aurora or blurred color backgrounds.
- A saturated blue primary button, send button or switch; colored drop shadows
  under buttons; soft floating shadows under inputs.
- A cloud of pill-shaped suggestion chips. Offer example prompts as a plain
  list with dividers.
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

## Research before you design

Use the living [design reference library](docs/design-references.md) to find
relevant catalogs and product examples. Consult what helps the current task
and maintain the directory as sources change; it supplements these rules
rather than adding another checklist.

Before building or changing a visible element, look up how established design
systems handle that element: Material Design 3, Apple's Human Interface
Guidelines, Shopify Polaris, WCAG 2.2, and products the seller already uses.
Look for sizes, spacing, touch targets, states and how the content is
arranged. Apply what fits Anteroom. When a finding conflicts with a rule
here, say so in the PR and update this file in the same PR. List what you
applied, what you did not and why, with links to the sources, in the PR and
in your reply to the seller.

The sidebar listing row is an example. Material 3 sets a three-line list row
at 88px with a 56px leading image and a 16px gap to the text. Polaris keeps
one thumbnail size throughout a list. So every row is 88px (96px on phones)
whether or not it shows marketplace logos.

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
- [ ] Input is acknowledged at once; waits show their status in place; a busy
      resource shows *Waiting* with **Cancel** instead of an error (see **How
      the interface behaves**).
- [ ] Works from the keyboard; touch targets meet the sizes above; new status
      changes are announced.
- [ ] Copy is short and in the seller's words; no repeated explanations.
- [ ] Researched how established design systems handle the element (see
      **Research before you design**); findings and sources are in the PR.
- [ ] Checked in the sandbox in dark and light, at desktop (1440×900) and phone
      (iPhone 15) sizes. Screenshots are in the PR and shown to the seller in
      your reply.

## Sources

- Linear, [How we redesigned the Linear UI](https://linear.app/now/how-we-redesigned-the-linear-ui)
  and [Behind the latest design refresh](https://linear.app/now/behind-the-latest-design-refresh)
- Vercel, [Web Interface Guidelines](https://vercel.com/design/guidelines),
  [Geist colors](https://vercel.com/geist/colors) and
  [Geist typography](https://vercel.com/geist/typography)
- Stripe, [Designing accessible color systems](https://stripe.com/blog/accessible-color-systems)
- Nielsen Norman Group, [10 usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/),
  [Response times: the 3 important limits](https://www.nngroup.com/articles/response-times-3-important-limits/),
  [Progress indicators](https://www.nngroup.com/articles/progress-indicators/) and
  [Error-message guidelines](https://www.nngroup.com/articles/error-message-guidelines/)
- Material Design 3, [Progress indicators](https://m3.material.io/components/progress-indicators/guidelines)
  and [Snackbar](https://m3.material.io/components/snackbar/guidelines)
- Apple, [Human Interface Guidelines: Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback)
- Shopify Polaris, [Toast](https://polaris.shopify.com/components/feedback-indicators/toast)
- W3C, [WCAG 2.2](https://www.w3.org/TR/WCAG22/) (2.5.8 Target Size, 4.1.3 Status Messages)
- The Fountain Institute, [Signs of a vibe-coded UI](https://www.thefountaininstitute.com/blog/signs-vibe-coded-ui)
