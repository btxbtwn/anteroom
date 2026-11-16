# Hands-on sandbox audit — 2026-11-16

Owner: sandbox-feature-behavior-copy. Baseline: `894241be30091dc615a97db8b0bb77933b34bef1` (also origin/main at start). This is a coverage record, not a replacement instruction framework. Follow [daily audit](../../audit.md), [design](../../../DESIGN.md), [performance](../../performance.md) and [responsiveness](../../responsiveness.md).

## Boundaries and checkpoints

- Disposable local sandbox reserved: API 4349, UI http://127.0.0.1:5194; data `/tmp/anteroom-audit-feature-4349`. Verified: status sandbox=true; process ANTEROOM_SANDBOX=1; isolated data folder; confirmed eBay list refused 403 before route execution.
- No live credentials, marketplace writes, messages, purchases, production changes or paid model calls. Browser uses incognito profile.
- User explicitly requires draft PRs, unmerged; this overrides repository auto-merge defaults.
- Prior coverage: [daily rollup #1544](https://github.com/btxbtwn/anteroom/issues/1544), baseline `2a257afb`, local/hosted/core/native automated journeys. Prior pass is not a pass in this run. No daily schedule changes.
- Dedup: #1554 eBay auth, #1555 Depop shipping, #1556 reconnect, #1557 validation timing, #1558 offer diagnostics, #1559 multi-size offer preview. Offer analytics and DB pool work owned elsewhere.
- Dependencies: npm ci passed (293 packages; audit reported zero vulnerabilities). Python editable dev+desktop install completed on Python 3.12. No aggregate suites started; reconnect owner notified to preserve validation queue.
- Native: local Linux reports “iOS Simulators need macOS with Xcode.” Mac hub lists an available audit iPhone 17 simulator, but no fixture runtime verified yet. Native app and Mac packaged checks NOT RUN. Phone web checks will not be labelled native.
- `.agents/skills/` absent in this checkout and no ancestor-local skill folder found. Canonical listing/sourcing/measurement SKILL.md consulted as behavior sources, not invoked for live work.

## Coverage matrix

Split rows into concrete controls and scenarios as exercised. PASS applies only to its named scenario; other states remain NOT RUN. Severity uses high (unsafe/data loss), medium (blocked/wrong behavior), low (copy/usability).

| Feature/control | Intended behavior and source | Scenario | Surface | Expected / actual | Status | Evidence | Severity / related PR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Sandbox isolation | sandbox.py, scripts/sandbox.sh | status + refused write + fake provider + temp folder | local API/web | Pending startup | NOT RUN | startup log pending | safety gate |
| Inventory search/filter/sold shelf | spec Listing Library; ListingSidebar, InventoryTablePage | populated/empty/search/status, refresh, Back | desktop + phone web | Pending | NOT RUN | — | — |
| New listing/chat/generate/edit | spec Workspace/Chat/Listing Editor | create/photos/generate/save/reload, validation, stop | desktop + phone web | Pending | NOT RUN | — | #1517 prior photo issue |
| Native marketplace tabs | spec marketplace forms; editorTabs | field order/category/errors, unavailable connection, confirmation | desktop + phone web | Pending; sandbox eBay specifics unavailable per audit.md | NOT RUN | — | #1554–1556 dedup |
| Bulk listing controls | Bulk* components; audit Tuesday | upload/edit/copy/delete/markdown/regenerate/templates, cancel/repeat | desktop + phone web | Pending | NOT RUN | — | — |
| Photos/measurements | spec Workspace, measure-this | upload/reorder/remove/edit, blanks, suggestions | desktop + phone web | Pending | NOT RUN | — | — |
| Integrations/onboarding | FirstRunGuide, SetupChecklist, settings/* | no provider/Chrome/accounts; blocked sign-in recovery | desktop + phone web | Pending; real OAuth out of scope | NOT RUN | — | #1554–1556 |
| Offers/automations | AutomationsPage + API; audit safety | fixture history, proposal/save/cancel, writes refused | desktop + phone web | Pending | NOT RUN | — | #1558–1559; analytics owner |
| Sales/delisting | item_status; sales routes; spec safety | fixture mark sold, edit/cancel/delete, delist refusal | desktop + phone web | Pending | NOT RUN | — | #1540 already merged |
| Sourcing | spec Sourcing; box-scout | empty/error/preferences/refresh/cart guard | desktop + phone web | Pending; outbound crawl intentionally blocked | NOT RUN | — | — |
| Analytics | AnalyticsPage; spec Ad spend/calendar | periods/unknowns/filters/drilldowns | desktop + phone web | Pending | NOT RUN | — | offer analytics separate |
| Business Assistant | spec Business assistant; AssistantPage | send/stop/history/note/proposal/refresh | desktop + phone web | Pending | NOT RUN | — | #1542 merged |
| Settings/help | SettingsPage; settingsNav; DESIGN copy rules | each section, validations/persistence, back, recovery instructions | desktop + phone web | Pending | NOT RUN | — | — |
| Hosted account recovery/isolation | cloud/SignIn; hosted sandbox docs | fixture login/reset/recovery/signout/two sellers | hosted desktop + phone web | No hosted sandbox started | NOT RUN | — | — |
| Native iOS + Mac | agent-sandboxes, iphone-parity | fixture journey, device-specific controls | native | Fixture runtime not verified | NOT RUN | device_list lists Mac hub | needs Mac follow-up |
| Concurrent responsiveness | performance.md + responsiveness.md | multiple sellers/drafts, p50/p95/errors | local/hosted/native | No measurements taken; validation resource queue | NOT RUN | — | ongoing DB investigation |

## Findings and ambiguities

- Product spec still calls the system “local-only” and describes one running Assistant conversation, while current code has hosted runtime and assistant threads. Treat as documentation drift to investigate, not grounds to remove shipped capability.

## Validation record

- PASS: dependency install `npm ci --prefix vendoo-studio`.
- NOT RUN: check.sh, pytest, Vitest, browser suites, hosted load test, native suites. This audit will not count existing tests alone as hands-on feature coverage.

## Checkpoint 05:10 UTC — observed, not aggregate-suite coverage

| Control/scenario | Intended source | Surface | Expected / actual | Result | Evidence / severity |
| --- | --- | --- | --- | --- | --- |
| Confirmed publish in sandbox | sandbox.py | local API | confirmed eBay list refused with explicit offline safety message, 403 | PASS | isolation-status.json; startup log /tmp/anteroom-audit-sandbox.log |
| Inventory fixtures | sandbox_seed.py | desktop web | four open (three active, one photos-only draft), twelve sold | PASS | browser initial screenshot mwisqtd6 |
| Automation picker + setup instructions | automation_scheduler.py vs automationRows.ts/AutomationsPage.tsx | desktop web | falsely promises every item waits; local edits/regeneration do not | FAIL (medium); fix implemented | browser type-picker/form inspected; focused regression red then 12 green; native source updated, native check not run |
| Empty scheduled Preview then Close | AutomationsPage | desktop web | shows Checking; Close exits without creating a task | PASS for cancellation only | browser action; preview validation outcome not inspected |
| Wholesale suppliers offline | box-scout + SourcingPage | desktop web | shows supplier errors, no fabricated recommendations, zero boxes | PASS for offline empty state | controls.tsv; technical socket exception in copy is a low-severity quality issue |
| Sourcing operating allowance | scout.py calculation, box-scout skill | desktop web | visible sold-piece label conflicts with usable-piece accessible label/help | FAIL (low) | controls.tsv |
| Add a box empty form | BoughtBoxes | desktop web | opens inline local form; Add box disabled until required values provided | PASS for empty validation | controls.tsv; actual create/cancel not yet checked |
| Scout empty | ScoutPanel | desktop web | asks for whole-item/tag photos and optional asking price | INSPECTED; submission NOT RUN | browser read |
| Sales negative buyer shipping | sales ledger | desktop web | negative amount rejected and form retained; Cancel closes | PASS | message: only net fees may be negative; Value error prefix is technical copy |
| Analytics missing data | AnalyticsPage | desktop web | missing cost/date disclosed; unknown distinct from zero; period comparison shown | PASS for fixture rendering | 8 last-30-day sales; no meaningful latency benchmark |
| eBay disconnected workspace | MarketplacesPage | desktop web | connect requirement; local calendar explicitly says seller runs sale separately | PASS for disconnected rendering | browser read; auth NOT RUN |
| All ten settings sections | SettingsPage/settingsNav | desktop web | read all sections, controls and instructions inventoried | INSPECTED; mutations NOT RUN | controls.tsv. General was also inspected. Earlier unsuccessful selectors were discarded as evidence; control inventory excludes those attempts |
| Bulk List everywhere | marketplace-actions confirmation | desktop web | dialog names live listing action; 0 of 5 selected; Cancel exits | PASS for explicit selection/cancel | screenshot mwisxq9v; no marketplace write |
| Photos-only draft generation | spec generation | desktop web | pressed Generate listing with two fixture photos | IN PROGRESS | ccf954b47750, fake provider; no live calls |

Read-only review also identifies a candidate stale Sourcing cart after refresh failure; not yet reproduced, so do not report it as a confirmed runtime finding. Source spec drift: hosted support and multiple Assistant threads supersede the older local-only/one-conversation prose. Native Sourcing is an existing parity gap per iphone-parity.md.

## Checkpoint 05:35 UTC — prioritized draft/list workflow

The audit is **not a comprehensive pass**. This checkpoint prioritizes Cristian's later create→list request and preserves the broader unfinished coverage. Baseline source is still `894241be`; sandbox-account fix commit is `8d4e6e87e0d4d0b8dff85225bd9fd193f03ca5b9`, draft [PR #1561](https://github.com/btxbtwn/anteroom/pull/1561). No PR is queued or merged by this audit.

### Completed named scenarios

| Feature/control | Intended behavior + source | Scenario / surface | Expected / actual | Status | Evidence / severity / related work |
| --- | --- | --- | --- | --- | --- |
| Generate listing | spec Generation; listing_generation | local desktop, two existing fixture photos | Fake generation saved a listing; missing native fields disclosed. Fake category selection is not real model quality. | PASS for saving; BLOCKED for real model/native-field quality | local `ccf954b47750`; completion took about 65 s, observation only |
| Title edit, history, reload | listing_editor; spec History | local desktop | saved title `Audit saved cotton shirt`; History compared previous/current; fresh page and API retained title and two photos | PASS for edit/compare/reload; restore NOT RUN | browser + compact API read |
| Direct marketplace tabs | editorTabs, native_editor | desktop eBay/Poshmark/Mercari/Depop/Etsy | observed available fields, gaps and recovery labels; eBay API category fields unavailable offline | PASS for inspected error/render states; BLOCKED for live-form equivalence | controls.tsv are observations, sometimes loading, not assertions that all controls work. Poshmark separately inspected |
| Search + table | ListingSidebar, InventoryTablePage | desktop populated and nonexistent-query search | table displays four fixtures; nonexistent query produces No matching listings; clearing restores fixtures | PASS | real browser. Filter/sort persistence still NOT RUN |
| Bulk Edit fields | BulkEdit | desktop select one fixture, replace Brand, review, save | preview includes dependencies; result 1 saved / 0 not changed; API brand `Fixture Brand`, original price/title retained | PASS | UI result + API. Raw JSON/internal names in review are low-severity usability debt, not redesigned here |
| Copy item | listing_copy | desktop fixture | creates independent local draft `f2d180b2a502`, with copied photos; no marketplace writes | PASS | browser + conversation read; all copy option combinations NOT RUN |
| Record a sale | marketplace_listings sold route | desktop copied fixture, Other/in person, $40 | saves local sale; copy becomes sold; original untouched by this operation | PASS | compact API status read. Sales delete/undo/delist UI NOT RUN |
| Automation approval instructions | scheduler executes local actions; explicit external approval | desktop + phone viewport | false blanket promise corrected; local changes save on schedule, external listing actions need approval | PASS after fix | screenshots/automation-{desktop,phone}.png; Vitest RED 1 failed/4 passed → GREEN 12 passed; native strings changed, native checks NOT RUN |
| Generation completion instructions | actual General/marketplace tabs, List/Update | local pre-fix desktop; hosted post-fix phone | removed Forms/Send directions corrected; newly generated hosted message uses actual control names | PASS after fix | summary regression RED 6 failed → GREEN 6 passed; old stored chat messages intentionally retain history |
| Help task instructions | helpTopics vs current controls | desktop expand Create listing; source review other topics | General/Chat, bulk More menu, History footer, Analytics views/labels, eBay calendar scope corrected | PASS for expanded topic; source/test coverage for others | helpTopics tests RED 3 failed/4 passed → GREEN 7 passed; not every linked workflow exercised |
| Sourcing operating cost | canonical scout calculation | desktop accessible field and help | per sold piece consistently; purchase cost per usable piece intentionally preserved | PASS after fix | rendered Sourcing test; no financial algorithm change |
| Failed refresh with changed ZIP | spec Sourcing cart guard | real browser with **Sourcing-only response fixture**, saved ZIP 90210/snapshot70115/refresh error | prior gate exposes old supplier cart URL; fixed gate removes href, disables Update needed, explains Update now. Restored original fetch after inspection | FAIL before / PASS after | screenshots/sourcing-before-phone.png; sourcing-after-{desktop,phone}.png. Actual page with test response; no supplier/cart request or purchase. Fixture figures are illustrative, not an audited recommendation |
| Failed refresh with changed budget; matching prefs; in-flight | SourcingPage gate | rendered component regression | stale budgets/ZIP disabled; matching-prefs failure retains cart; refresh blocks links | PASS | RED 2 failed/20 passed → GREEN 22 passed, single Vitest worker |
| Sandbox Claude account status | sandbox fake account boundary | local onboarding then API after restart | pre-fix real machine account identity displayed; subprocess status escaped in-process socket fence. Fixed before probing/cached identity access; signed out/null email/null plan | FAIL before / PASS after (high isolation issue) | account identity excluded from evidence. Regression RED 2 failed → sandbox+Claude tests GREEN 50 passed; #1561 |
| Connect Chrome | sandbox refusal | local desktop Connections | request refused, Could not open Chrome plus explicit offline explanation | PASS | no Chrome launched |
| First-run guide | FirstRunGuide | local desktop | Welcome→AI→Brave skip→Chrome skip inspected. Provider settings not changed. Stopped for identity isolation investigation | PASS for inspected navigation; remaining tour/create steps NOT RUN | no screenshot containing machine identity retained |
| Hosted auth invalid password | SignIn + local protocol stand-in | desktop at **127.0.0.1:5195** | incorrect fixture password gives mismatch + reset action | PASS | localhost alias instead gives CORS failure; use printed launcher address. This is a sandbox URL limitation, not production auth evidence |
| Hosted recovery | SignIn + hosted_sandbox LocalSupabase | desktop request reset→manual `?code=recovery-alice`→save new password | opens Choose a new password and returns Alice to workspace | PASS for UI/protocol | stand-in sends no email; this does not verify live email delivery or real Supabase password persistence |
| Sign out / second seller | cloud AccountMenu | Alice sign out→Bob sign in, phone viewport | Bob sees zero inventory/chats; no Alice cached rows remain visible | PASS for this sequential context | independent simultaneous browser-context check NOT RUN |
| Hosted phone create/upload/generate | draft/photo routes + fake provider | Bob create draft, General Add Photos picker, one disposable JPEG, Chat Generate listing | creates `d65d5f36c9f4`, stores photo and generated draft; missing marketplace fields explained | PASS for draft creation | photo tray path tested; known chat attachment #1517 NOT re-tested or fixed |
| Hosted phone edit/reload | listing_editor | edit title→blur→reload | `Hosted phone audit saved shirt` and one photo retained; no alert on save | PASS | browser field assertion; underlying API snapshot used for safety probe |
| Hosted phone List prerequisite | hosted Chrome notice | choose Poshmark→List… | opens account/extension connection panel because no Chrome; no simulated browser publish adapter configured | PASS for prerequisite; BLOCKED for browser publication success | screenshots/hosted-phone-connection.png. The account popup is intended connection recovery, not a misdirected click |
| Hosted ownership + guards | sandbox, seller-scoped drafts | Bob own read vs Alice; named confirmed Poshmark list/delist | Bob200, Alice404; both writes403 | PASS | hosted-safety.json, ephemeral tokens never printed/stored |
| **Simulated complete listing journey** | real API routes + native_marketplaces | isolated test process, temporary DB/photo store, fake AI/bridge, sockets forbidden | create→JPEG upload→generate→edit→validate→missing confirm400/all-marketplace422→named confirmed Poshmark rejection→explicit retry→simulated published adapter response→fresh client reload→effective active status | PASS, **simulation only** | new `test_fixture_create_generate_edit_confirm_list_failure_retry_and_reload`; no real marketplace listing exists. Schema/comp external reads replaced with fixtures |
| List cancellation | native_marketplaces cancellation | isolated fixture adapter | seller cancels while Mercari draft saves; only save_draft executes, no publish; job cancelled | PASS | existing `test_cancel_stops_a_running_list_before_it_publishes` run alongside new journey; 2 passed/66 deselected in 9.18 s |
| Historical repricing lead | 2026-10-28 report on separate machine 0.1.492, **unconfirmed now** | current desktop API, two independent TestClients | price37 saved/reloaded; stale second-editor45 refused409 with Reload; negative price saved as incomplete with validation feedback; correcting39 saves/reloads; no adapter calls | PASS for current local API, no current defect reproduced | new `test_current_reprice_saves_reloads_and_recovers_from_invalid_and_stale_edits`: 1 passed/68 deselected in 7.04 s. Independent browser/device UI repricing and hosted two-seller repricing NOT RUN |

### Verification and resource state

- PASS: npm dependency setup, Python editable setup, targeted Ruff/oxlint, `git diff --check`, `scripts/check-design.py` (no new violations).
- Targeted checks actually run are recorded above. The first workflow-test attempts failed due to test assertions (completion message is persisted rather than raw SSE, and effective inventory status differs from internal conversation draft state); corrected assertions then passed. Those were **not product bugs**.
- NOT RUN locally: `./check.sh`, full frontend/backend/browser suites, build, type/dead-code/architecture aggregate checks, Postgres regression suite, load test, native tests. Preserve shared validation queue and disk pressure; CI is tracked separately, not represented as a local run.
- All own sandbox processes, ephemeral Postgres and browser tab released at checkpoint. No listening reservations on 4349/5194 or4350/5195. No schedules changed. No new installs after disk pressure warning.
- Screenshot checks are Chromium 1440×900 and CSS phone393×852, light. **Not WebKit, not a native iPhone, not a packaged Mac app. Dark mode is NOT RUN.**
- The machine account identity is not in committed screenshots or notes. Only fake seller emails/item photos appear in retained artifacts. Raw log/DOM dumps are not committed; controls.tsv retains only named controls and observed state.

### Remaining audit surface (NOT RUN unless explicitly listed above)

Bulk upload/folder grouping and interruption; bulk markdown/copy/delete/regenerate/marketplace dialogs beyond named scenarios; templates apply/save; photo reorder/crop/background/video/remove/undo and measurements; full native form controls/options; UI history restoration; filters/sort/selection/scroll persistence and back/forward; duplicate search; sourcing actual sold evidence and all buying-record edits; Scout photo check/failure recovery; Assistant proposals/automation cards/memory/attachments/history deletion; automation save/enable/pause/dry-run/run-now/history and all offer/sharing confirmation states; sale record edit persistence/delete/undo; calendar create/edit/record/delete; settings mutations/backup restore and invalid paths; native iOS/Mac; broad empty/loading/permission/disconnection/repeat/cancel states; two simultaneous browser/seller contexts; p50/p95 under multiple sellers/drafts. These gaps prevent full audit sign-off.

Native follow-up requested through the parent thread. Mac hub now lists devices, but no verified fixture app/API was launched. Do not treat available device discovery as a native pass. Existing #1517 and #1485 remain unresolved leads; #1556 merged while this audit ran, but this checkout's baseline does not include it. Offer analytics #1560 is owned separately and not counted as audited here.

### Production boundary

[Conditional production readiness](production-readiness.md) preserves Cristian's later listing-and-confirmed-delisting intent. No genuine item, marketplace/account scope, fee budget or deployment has been verified. No production action, live credential, real provider request, or real listing occurred. Hosted web delivery and installed desktop delivery require separate version evidence.

### Applied design research

Applied clear control names and actionable failed-state instructions from [W3C labels/instructions](https://www.w3.org/WAI/WCAG22/Understanding/labels-or-instructions) and [Apple writing guidance](https://developer.apple.com/design/human-interface-guidelines/writing). Kept existing components and styling; no layout/color/type-system redesign was warranted. Sources consulted 2026-11-16; this is not an accessibility conformance audit.
