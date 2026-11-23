# Design reference library

A living directory of places to find UI/UX examples, not another design system
or acceptance checklist. Use it to investigate the problem in front of you;
there is no required number of sources or prescribed tour through the list.

## Start with the existing guidance

- [DESIGN.md](../DESIGN.md) owns Anteroom's web UI visual and interaction rules,
  shared components, research workflow and review checklist. Its
  [sources](../DESIGN.md#sources) hold the design-system and accessibility
  references; they are not duplicated here.
- [AGENTS.md](../AGENTS.md) owns engineering and product invariants;
  [the product specification](../ANTEROOM_SPEC.md) describes the product
  and architecture. For current web styling, follow `DESIGN.md`, not older
  visual descriptions in the specification or audit snapshots.
- [Performance goals](performance.md) applies McMaster-Carr/NextMaster's speed
  and responsiveness aspirations to every change across web, Mac and iPhone;
  these are performance references, not visual templates.
  [Responsiveness](responsiveness.md) holds the observations, measurements and
  remaining performance work.
- [Desktop UI reference](desktop-ui-reference.md) records a versioned audit
  for the native port, while [iPhone parity](iphone-parity.md) tracks that
  port's coverage. Consult them for Anteroom-specific context, not as an
  external inspiration gallery or proof of current behavior.

## Catalogs and examples

These starting points cover different kinds of research. Descriptions identify
the source; suggestions describe how it could help Anteroom, not a claim that
an individual example has been tested here. Catalogs and access plans change;
some browsing, search or full examples may require an account or paid access.

| Source | What it contains | When it may help |
| --- | --- | --- |
| [Mobbin](https://mobbin.com/) | A library of shipped app and website screens, flows and UI elements. | Compare how real products arrange navigation, settings, editors and mobile states; inspect surrounding screens rather than copying an isolated screenshot. |
| [Refero](https://refero.design/) | A searchable collection of real product interfaces and user flows, with examples organized by UX and UI elements. | Find alternative treatments for a specific task, such as search, editing or calendar navigation, and compare information hierarchy. |
| [UI Patterns](https://ui-patterns.com/patterns) | A pattern catalog spanning input, navigation, data and other interaction problems. | Find names and alternatives for a problem, such as autosave, undo, table filters or progressive disclosure, before choosing an implementation. |
| [Curated](https://curated.design/) | A gallery of websites and page sections organized by industry and style. | Explore public-facing page composition and typography. Marketing-page treatments need particular scrutiny before borrowing them for a dense seller workspace. |

## Reference products

Products to compare against again and again: they solve Anteroom's problems
for sellers, or they are dense workspaces whose look Anteroom follows. Each
row says where the product records its changes, so a finding can cite a dated
entry instead of an undated screenshot. Listed 2026-11-23; check the source
before relying on an entry, because products rename, move and archive things.

**Cross-listing and seller tools.** The seller already knows these screens.
Study their task flows (photo intake, bulk edit, delist, sale detection), not
their styling.

| Product | Source | Where changes are recorded | Study it for |
| --- | --- | --- | --- |
| [Vendoo](https://vendoo.co) | Closed | [Help center](https://help.vendoo.co) and [blog](https://vendoo.co/blog); no dated changelog | The cross-listing flow sellers move from; Anteroom imports its history. |
| [List Perfectly](https://listperfectly.com) | Closed | [Blog](https://listperfectly.com/blog/), including monthly reseller news | Bulk listing and marketplace coverage. |
| [Crosslist](https://crosslist.com) | Closed | [Dated changelog](https://feedback.crosslist.com/en/changelogs) | Small, frequent improvements to a cross-listing editor, with dates. |
| [Nifty](https://nifty.ai) and [Flyp](https://tools.joinflyp.com) | Closed | Their own blogs; no public changelog | Auto-delist and sale detection as sellers see them. |
| [eBay Seller Hub](https://www.ebay.com/sellercenter) and [Etsy](https://community.etsy.com/t5/Announcements/bd-p/announcements) | Closed | [eBay announcements](https://community.ebay.com/t5/Announcements/bd-p/Announcements), Etsy announcements | Changes to the marketplace forms Anteroom replicates. Confirm a form change with the form sweeps, not the announcement. |
| [Shopify admin](https://www.shopify.com/editions) | Closed admin, public components | [Editions](https://www.shopify.com/editions) (twice-yearly releases), [developer changelog](https://shopify.dev/changelog), [Polaris web components](https://shopify.dev/docs/api/app-home/polaris-web-components) | Seller admin conventions: product lists, inventory, orders, bulk actions. Polaris React is [archived](https://github.com/Shopify/polaris-react-archive); its [what's new](https://polaris-react.shopify.com/whats-new) records the move. |

**Dense workspaces Anteroom looks like (closed source).**

| Product | Where changes are recorded | Study it for |
| --- | --- | --- |
| [Linear](https://linear.app) | [Changelog](https://linear.app/changelog) and [design posts](https://linear.app/now) | Sidebar, list density, keyboard use, command menu. |
| [Vercel dashboard](https://vercel.com/dashboard) | [Changelog](https://vercel.com/changelog), for example the [navigation redesign rollout](https://vercel.com/changelog/dashboard-navigation-redesign-rollout) and [why it was redesigned](https://vercel.com/blog/dashboard-redesign) | Sidebar navigation, mobile navigation, measured speed gains. |
| [GitHub](https://github.com) | [Changelog](https://github.blog/changelog/); its components are open in [Primer React](https://github.com/primer/react) | Lists, filters, tables and issue views at scale. |

**Open source.** The code and its history are public: read how a pattern is
built and the pull request or release that changed it.

| Project | Code | Where changes are recorded | Study it for |
| --- | --- | --- | --- |
| T3 Code | [pingdotgg/t3code](https://github.com/pingdotgg/t3code) | [Releases](https://github.com/pingdotgg/t3code/releases) and pull requests | Chat, sidebar and information hierarchy that Anteroom's workspace follows (see `ANTEROOM_SPEC.md`). |
| Medusa admin | [packages/admin/dashboard](https://github.com/medusajs/medusa/tree/develop/packages/admin/dashboard) | [Releases](https://github.com/medusajs/medusa/releases) | An open commerce admin: products, variants, inventory, orders. |
| Plane | [makeplane/plane](https://github.com/makeplane/plane) | [Releases](https://github.com/makeplane/plane/releases) | Linear-style lists, sidebar, filters and views. |
| Twenty | [twentyhq/twenty](https://github.com/twentyhq/twenty) | [Releases](https://github.com/twentyhq/twenty/releases) | Record tables, inline editing, kanban and detail panes. |
| Supabase Studio | [apps/studio](https://github.com/supabase/supabase/tree/master/apps/studio) | [Changelog](https://supabase.com/changelog) | Dashboard navigation, data tables and settings. |
| Base UI | [mui/base-ui](https://github.com/mui/base-ui) | [Releases](https://base-ui.com/react/overview/releases) | The component library Anteroom's `ui/` components use: behavior, focus and accessibility. |
| shadcn/ui | [shadcn-ui/ui](https://github.com/shadcn-ui/ui) | [Changelog](https://ui.shadcn.com/docs/changelog) | Tailwind compositions of the same primitives, including its sidebar. |
| GOV.UK Frontend | [alphagov/govuk-frontend](https://github.com/alphagov/govuk-frontend) | [Releases](https://github.com/alphagov/govuk-frontend/releases) | Forms, errors and validation that were tested with users; see GOV.UK Design System under Research. |

### Show what changed in a closed product

Cite a dated changelog entry, release or blog post. When there is none,
compare two [Wayback Machine](https://web.archive.org/) captures of the public
page and link both. Note the date you looked. A screen seen only while signed
in is evidence of that date and account, not of a deliberate change.

## Research

Sources that measured or tested something. Prefer them over opinion when
choosing a timing, size, pattern or wording, and link the finding in the PR.

| Source | What it contains | When it may help |
| --- | --- | --- |
| [Baymard Institute](https://baymard.com/blog) | Large-scale, benchmarked e-commerce UX research: usability-tested guidelines on navigation, hover menus, search, filtering, product lists, forms, checkout and mobile. Articles are free; the full guideline database and benchmarks need a paid account. | Find measured findings (such as the 300–500 ms hover delay in [drop-down menu flickering](https://baymard.com/blog/dropdown-menu-flickering-issue)) before choosing timings, filter and list layouts or form behavior. Its findings are from shopper-facing sites; check that they carry over to a seller's workspace. |
| [Nielsen Norman Group](https://www.nngroup.com/articles/) | Usability research articles; the ones `DESIGN.md` relies on are in its [sources](../DESIGN.md#sources). | Heuristics, response times, navigation, forms and error messages. |
| [WAI-ARIA Authoring Practices Guide](https://www.w3.org/WAI/ARIA/apg/patterns/) | W3C's keyboard and screen-reader behavior for each widget, with working examples. | Any menu, dialog, tab list, combobox, disclosure or tree: how keys and focus must behave. |
| [GOV.UK Design System](https://design-system.service.gov.uk/) | Components and patterns built from user research. Each page lists its recent changes and links to a public discussion of what research found; [issues](https://github.com/alphagov/govuk-design-system/issues) hold the backlog. | Forms, validation, error summaries and question pages. |
| [U.S. Web Design System](https://designsystem.digital.gov/) | Research-backed components with accessibility test notes. | A second tested source for forms and navigation. |
| [Inclusive Components](https://inclusive-components.design/) and [The A11y Project](https://www.a11yproject.com/) | Worked accessible builds of common components; accessibility checklists. | Building a component Base UI does not provide, or checking one that it does. |
| [MeasuringU](https://measuringu.com/blog/) | Quantitative UX research: task success, benchmarks, questionnaires, sample sizes. | Planning or reading a measurement, such as how many sellers a test needs. |
| [Laws of UX](https://lawsofux.com/) | Short summaries of psychology findings (Fitts's, Hick's, Doherty threshold) with their original sources. | A quick reminder; cite the original source it links, not the summary. |
| [web.dev Core Web Vitals](https://web.dev/articles/vitals) and [Web Almanac](https://almanac.httparchive.org/en/2024/) | Google's loading and interaction metrics and thresholds; HTTP Archive's yearly measurements of the web. | Setting and checking speed targets alongside [performance goals](performance.md). |
| [Smashing Magazine UX](https://www.smashingmagazine.com/category/ux/) | Long-form articles that collect research and practice, such as [hover menu timing](https://smashingmagazine.com/2021/05/frustrating-design-patterns-mega-dropdown-hover-menus/). | Collecting sources on a pattern; follow their links to the research. |

## Use and maintain this library

Start from the seller's task and choose whichever source adds useful context.
Search beyond this list when it does not cover the problem. A familiar pattern
can be useful without being appropriate everywhere: compare the task, device,
content density and states, then use the research workflow in
[DESIGN.md](../DESIGN.md#research-before-you-design) to explain the choice.
An attractive screenshot is not evidence of accessibility, responsiveness or
successful recovery from an error. Inspect the actual interaction when needed.
External examples do not override Anteroom's rules or authorize a new library,
service, subscription or integration.

Agents and contributors should add, revise, reorganize or remove entries as
they discover better sources, changed access or stale links. Keep each entry
short: a working source URL, what it contains and why someone would consult it.
Prefer specific example links when a useful finding warrants retaining one;
include the context and observation date when the finding depends on a version.
Do not retain a link just to grow the list, or treat a blocked fetch as proof
that a site is dead. Note an access limitation when useful and choose another
accessible source for the current task.

Keep durable rules in `DESIGN.md`, feature requirements in the product spec,
performance goals in `performance.md`, evidence in `responsiveness.md`, and
port status in the parity tracker. Link to those documents instead of copying
their contents here.
Changes to this directory alone do not change those requirements.
