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
  [the product specification](../VENDOO_STUDIO_SPEC.md) describes the product
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
