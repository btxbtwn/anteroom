> Current status counts come only from [scenarios.tsv](scenarios.tsv). Earlier checkpoints below are historical and superseded where later evidence exists. PASS is limited to each named scenario and surface; it is not a full-feature or release sign-off.

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

## Checkpoint 05:50 UTC — resumed audit and independent review

Canonical current inventory: **97 named scenarios: 38 PASS, 3 FAIL, 8 BLOCKED, 48 NOT RUN**. This is a scenario count, not a button/route denominator or test count. Some PASS rows cover rendering/API/unit fixtures only; their surface and excluded behavior are explicit in scenarios.tsv. Historical failed regressions fixed in the draft stack are not counted as currently failing. The three current FAIL rows are low-severity seller-copy/usability issues already observed: raw bulk-review JSON, supplier socket exception text, and the Value error prefix in sale validation. None is evidence of unsafe publishing.

PR state checked at resume:
- #1561 exact head `8d4e6e87e0d4d0b8dff85225bd9fd193f03ca5b9`: CI green; draft/open; no GitHub reviews/comments.
- #1562 exact head `715376208304b9571491d5a2183adf7d20b3ff17`: frontend/code health/SQLite/Postgres/hosted browser passed; sandbox browser still running at latest observation. Draft/open, stacked on1561; no GitHub reviews/comments. No red job to reproduce or restart.
- Independent read-only reviewer examined both exact heads and found one P2 copy error introduced in Help: it required eBay→Marketing even for a disconnected account, whose calendar renders directly. Corrected to find Sale calendar under eBay and mention Marketing only for connected accounts. Focused Help check RED1 failed/6 passed → GREEN7 passed. Browser recheck of this wording awaits the reserved interactive slot; no runtime behavior changed.
- Reviewer found no other production-code blocker in the Claude guard or Sourcing stale-cart gate. Advisory validation weakness fixed: the simulated journey now explicitly asserts `can_send == false` and nonempty errors for its intentionally incomplete generated fixture. Targeted rerun:1 passed/68 deselected in9.69s. This tests gap reporting and simulated listing plumbing; **it does not establish real marketplace readiness**.
- Reviewer advised canonical counts to avoid mixing old checkpoint states; scenarios.tsv now owns those current counts. Independent review is local agent review, not a GitHub approval.

Resource agreement: disk4.8GB free/96% at resume; no installs/full suites. Shipping retains its full browser invocation, then #1560 has a reserved short real-Postgres/desktop-phone/light-dark slot, then eBay. Audit has no browser/Postgres reservations and will wait for an explicit release, with no polling or process interference. Only the changed Help check and one strengthened workflow case ran, each single-worker, under10s; unchanged scenarios were not rerun.

Concrete native requirements and narrowed existing test cases are in [native-follow-up.md](native-follow-up.md). VPS cannot execute them. Parent routing still requires a verified sandbox Debug app/API and device session; hosted release identity and installed desktop identity remain separate.

Next interactive priority once released: browser repricing and stale second editor; History restore/confirm/reload; template save/apply; then bulk upload/markdown/delete undo and remaining cancellation/permission states. Do not repeat the verified generation/title workflow unless changed code warrants it. Production remains blocked as recorded in production-readiness.md.

## Checkpoint 06:10 UTC — scoped copy fixes while resource slot is reserved

Current canonical counts: **38 PASS / 0 FAIL / 11 BLOCKED / 48 NOT RUN**,97 named scenarios. The three prior low-severity copy failures are now implemented and covered by focused regressions, but move to BLOCKED pending actual browser rechecks rather than being counted as fully passed. This is not a comprehensive sign-off.

- Read-only exact-head CI check: #1561 `8d4e6e87e0d4d0b8dff85225bd9fd193f03ca5b9` all selected/required checks green, draft/open, no GitHub reviews; #1562 `0bd8c7783156b0997c41ef441f7eb12ecd68b2bc` green except sandbox browser still running, draft/open, no GitHub reviews. No red job or forced rerun. New source changes below have not inherited that earlier head's CI results.
- Disk4.7GB free/96%; no installs, browser, Postgres or aggregate suites. #1560 released explicitly to eBay. Audit requested explicit next handover from eBay and retains no claim until release; no polling or process interference.
- **Bulk review:** root cause was whole-object JSON serialization with internal metadata. It now uses the established revision-history diff/field labels to show individual changed nested fields and omit private preparation metadata. Original review/apply tokens remain untouched. Production-component render regression failed before, passes after; nested-diff helper regression also passes. Frontend2 tests passed, one worker,1.70s.
- **Supplier errors:** root cause was interpolating exceptions into seller-facing snapshot messages. Seller copy now names the supplier and says to try Update now later; original exception detail remains in internal logs. This also avoids inviting an immediate retry for429 cooldowns. Existing failed-supplier test strengthened and observed failing before. Backend targeted green2 tests (supplier + initial money case),5.69s. The broad selector's RED run executed the inherited supplier test in three classes (4 failed total); GREEN used the exact RefreshTest node to avoid duplicate work.
- **Money validation:** root cause was Pydantic's generic ValueError prefix, not a broken amount rule. A typed custom validation error retains the amount instructions and accepted values without that implementation prefix. RED case failed, then GREEN; additional focused non-string/negative-input checks assert valid four-decimal shipping and negative net fees unchanged. Final money2 cases passed in6.27s. No financial calculations/persistence/marketplace operations changed.
- Independent read-only review of these uncommitted changes found no remaining blocker; its rate-limit wording concern was accepted. It confirmed diff tokens and diagnostic logging are preserved. The rendered component test depends on hook order (advisory brittleness); the independent pure-diff test also checks semantics. No reviewer tests, browser sessions or duplicate implementation.
- Targeted Ruff/oxlint, TypeScript `tsc -b --pretty false`, diff check and design check passed. No new design violations. Full build/suites/native execution remain NOT RUN. No source or screenshots from another owner's server were used.

Native source caveat: BulkEditView currently prints raw field paths and renders object values as an ellipsis, not the web's raw JSON. It needs its own native fixture inspection; the web fix is not native proof. This remains within the existing native BLOCKED scenario and is added to native-follow-up.md.

Next browser slot: verify these three fixed copy paths at desktop/phone (including dark where feasible), then current browser repricing/history restore/templates. Production conditions remain unchanged: no genuine item selected, marketplace/account/fees unresolved, relevant deployment unverified, independent post-delist readback still required. No production mutation.

## Historical verification lead — 2026-09-14 report, queued only

Cristian supplied [historical T3 source](https://app.t3.codes/threads/e76002be-581a-4294-afd4-0611f3ab299d/92bd770e-35f5-47b6-85d2-2d28327ebcf9): an earlier Vendoo Studio audit allegedly counted absent navigation/live-listing status as draft success and observed blank Mercari fields until load/reopen. **Not a confirmed current defect; do not assume the same state model.** The current T3 reader returned `thread_not_found`, “Thread … is no longer available.” Only the user-supplied summary is available; no claim that the historical report itself was read.

Source inspection before queuing cases:
- `services/native_marketplaces.py:_outcome` compares an explicit successful state against draft/published according to the action; mismatches become unknown/uncertain. Non-Depop publication requires a listing link. These branches are useful guardrails, not proof of browser verification.
- `ListingEditor.tsx:formStatus` distinguishes a first form fetch from a failed load and offers reopen/reload recovery. This does not prove delayed Mercari values display correctly; an empty initial render is not evidence that saved values are gone.
- Existing missing/unknown/cut-off adapter cases exist in test_native_marketplaces.py; none were rerun just for this lead. New cases will reuse that isolated fake-adapter fixture and actual browser delayed responses after the agreed handover, with no live accounts.

Added 3 NOT RUN scenarios to scenarios.tsv: missing explicit adapter state, stale/ambiguous status, and Mercari delayed-load/reopen. Required fixture evidence: absent/contradictory states never show verified draft/listed success; explicit draft/not-listed remains distinct from unknown; held form fetch shows loading, then populated values; navigation/reopen/reload preserves values and does not save blank placeholders. Exercise empty and error responses too.

Canonical inventory is now 100 named scenarios: **38 PASS / 0 FAIL / 11 BLOCKED / 51 NOT RUN**. No new execution is claimed. Queue remains eBay→#1563 full check→audit, subject to coordinator handover. No audit browser/Postgres reservation or production action.


## Bounded interactive checkpoint — 08:27–08:46 UTC

Source: pushed PR1562 head `63b6765e06b4c39a188f77c4208a15b3fbf5ae1d`, local documentation commit `8b4f1f56`, plus scoped protected-field copy fix described below. Version API 0.1.983. One Chromium tab only, desktop 1280/1440 CSS pixels and 390×844 phone CSS viewport; this is neither WebKit nor native iOS. No installs, Postgres, full suites, production accounts or model requests.

Isolation: API4349/UI5194, fresh `/tmp/anteroom-audit-slot-0827`, status sandbox=true/data_dir matches, extension disconnected/Chrome unavailable, fake provider. Explicit named confirmed Poshmark List returned403 with sandbox refusal. Internal logs showed outbound socket refusal; no live listing created. Active fixture rows were seeded simulated states, not marketplace evidence. Temporary window.fetch overrides changed only read responses in the owned tab and were restored; closing the tab removes them.

Verified:
- Supplier copy: real offline errors now give supplier name and Update now recovery without socket/IP details. Desktop light and phone dark captures: sourcing-copy-*.png.
- Bulk review: brand and size field previews readable; confirmed brand save reported 1 saved and later editor/API reload retained it. bulk-review-*.png. Nested object flattening remains additionally supported by existing focused helper regression; this run's real previews contained simple brand/size changes.
- Sale validation: -2 buyer shipping rejected without generic Value error prefix; 4.1234 correction saved and reopened unchanged. sale-error-*.png (both actual dark; no claim of light sale capture).
- General price browser save is on blur, not an explicit Save button in the current web editor: 37 persisted after reload; -1 entered and then corrected to39, populated value independently confirmed on desktop/phone. Dedicated invalid-feedback/interrupted/stale-context browser matrix remains pending. Presence of an initially empty control was not counted as saved-value verification.
- History restore: selected earlier37 version, required Restore this version then Confirm restore; independent fresh API and reopened browser showed37. Restore says photos are excluded. Subsequently changed back39 as disposable test input.
- Mercari form: held first GET gave explicit loading; release showed saved title/39. Fresh-cache injected503 showed error, Reload fields recovered. Empty fields response showed no controls; no blank placeholder was saved; reopening with real sandbox response retained title/39. Empty response lacks explanatory copy, but no current server route producing that exact valid empty schema was established: robustness lead only. mercari-loading/loaded/form-error/empty/reopened screenshots.
- Injected unknown Mercari native status displaced the prior seeded Listed with Unconfirmed, Check result and manual-check controls. No check/publish/manual success was clicked. Missing-state adapter matrix and explicit draft-vs-unknown/stale cases remain incomplete. mercari-unconfirmed-desktop.png.

New observed low-severity copy defect: History exposed seller_fields arrays containing internal field paths. Root cause: existing review formatter treated protection metadata as ordinary JSON. Final fix retains the entire diff and restore eligibility, labels it Fields protected from automatic edits and maps values to readable field labels in both review components. First approach hiding metadata was rejected in independent review because lock-only restores would become disabled; it is not in the final change. Final lock-only preservation/render regression failed before the final fix and passed afterwards: **9 tests across listingChanges.test.ts and bulkEditReview.test.ts** using one worker. Focused oxlint and git diff check passed. Final independent static review found no blockers; no GH approval implied. Screenshots history-internal-before-phone.png and history-protection-desktop/phone.png. Full checks/types/build not run locally in this slot; exact-head CI must be checked after push.

Infrastructure interruption: initial shell session exited143, backend logged clean shutdown at08:41:21, signal source unverified. Browser then showed Loading listing workflow with refused API connections; do not treat this as a product defect. Restarted same disposable folder with --keep, reverified sandbox status and completed copy captures. Both owned servers stopped, preview tab closed, ss confirmed4349/5194 free. No owned tests/PG running. Explicit mode:auto release sent to shipping for root-prioritized short permission probe before broad suites, superseding earlier direct sign-in handoff.

Canonical totals: **46 PASS / 0 FAIL / 8 BLOCKED / 49 NOT RUN**, 103 named scenarios. No comprehensive signoff. Next reserved audit phase should finish missing-state/draft/unknown matrix, browser stale/invalid/interrupted saves, templates and bulk workflows; native checks remain with parent/Mac. No production mutation.

Historical cleanup lead added after release: see production-readiness.md. Current Depop source attempts URL recovery but still allows a completed publication without a URL; delist requires a durable identifier. This is a readiness blocker/lead, not proof that the historical marketplace failure persists. No tests or live actions executed for it. Canonical totals after this addition: **46 PASS / 0 FAIL / 8 BLOCKED / 50 NOT RUN**,104 scenarios.

## Exact-head CI checkpoint — 2f9b662f

PR1562 remains OPEN/DRAFT on `2f9b662fc006d68e7b4a26ce7a37d684ce33fa20`, stacked on audit/sandbox-claude-isolation. Read-only GitHub verification confirmed required CI SUCCESS: frontend, local and hosted sandbox browser jobs, code health, SQLite/backend and Postgres shards, repository/changelog checks. Sixteen checks are complete: twelve SUCCESS and four intentionally SKIPPED (dependency audit, extension, webhook relay, Supabase preview). No GitHub review submissions; prior independent static reviews remain separate evidence. No branch update or CI rerun needed.

Ledger remains 46 PASS / 0 FAIL / 8 BLOCKED / 50 NOT RUN. CI does not advance manual/native coverage or establish live publish/delist readiness. No resource reservation held; sign-in owns its retrospective PG/browser stage, followed by1559→bounded replay→1560 full check/benchmark→1557 retrospective. Await new explicit audit browser handover; no overlap, production action or merge.


## Resumed audit — Depop identity regression

Preserved clean audit branch at6368e120 and drafts1561/1562 unmerged. Actual resumed thread list shows newly running workers despite earlier zero-worker report; process scan found no current suites/API/vite, only unrelated Nov14 Postgres/Nov8 sandbox/Nov9 preview Chrome, all untouched. Disk8.5GB. Requested explicit interactive reservation from coordinator before browsers; no PG/install/full suite launched. No .agents directory exists in this checkout; repository skill sources remain under skills/.

New isolated real-route regression failed on old code: Depop adapter ok/published without listing_url was recorded published with listed_price/time, while delist had no identifier. Minimal fix removes that exception; existing error handling records unknown/uncertain, blind List retry409, missing-ID delist409. A read-only marketplace.result check can recover the known product URL without another publish. Positive fixtures now include a valid Depop product URL.

Durable fixture lifecycle: fake adapter save_draft→explicit publish returns product URL, expiry/reload through a fresh ORM session and API client derives expected product slug, named confirmed delist targets the same ID, fresh API reads deleted, independent fake-marketplace HTTP GET reads listed before and absent after. Socket connects forbidden. No real marketplace, guard change, or real model. This proves simulated lifecycle only, not a full process restart.

Validation: initial RED1/PASS1; corrected targeted8 +12 subcases passed. Both affected backend files then passed100 tests +18 subcases with six existing SQLite datetime deprecations and one AsyncMock warning. The warning was isolated to an existing category-gap test returning default AsyncMock instead of a dict; its realistic refusal fixture now yields clean targeted4 +12 subcases. A tracing attempt was interrupted before collecting tests (no pass claimed). Ruff and diff checks passed. Independent read-only review found no blocking issue; it explicitly identified legacy stored published/no-link records as unresolved, now a separate pending row. No native checks or current browser passes claimed for the new fix yet.

Current totals: **48 PASS / 0 FAIL / 8 BLOCKED / 49 NOT RUN**, 105 scenarios. Remaining work continues after resource handover; no comprehensive or production signoff.

Source inventory while browser resources are reserved: replaced generic references for browser save/navigation/template/bulk scenarios with actual implementation/control contracts. Corrected audit's own mistaken bulk-delete Undo assumption: BulkDeleteDialog explicitly says irreversible and services/bulk_delete.py permanently deletes after reviewed confirmation. Do not change the app to match the mistaken ledger. Source-only copy lead: BulkMarketplaceDialog description says items run one at a time, but bulkMarketplace.runMarketplaceTasks uses three workers for List/Update and serial for other actions. No new browser observation, regression, code fix or passed status is claimed yet.


## Remaining-surface source contracts and CLI boundary

Read-only independent inventory supplied exact photo/measure/scout/sourcing/assistant/settings sources and controls, now merged into the sole scenarios.tsv ledger. No source-only observation counted as a UI pass. Corrected nonexistent Undo/filter/export/Restore controls in planned scenarios: photo removal is permanent, log page only expands details/Clear, and restore is a stopped-server script. Background cutout success needs Mac14+; Linux exercises refusal/recovery, hosted omits local video rules. Deduplicate #1571 automation card lifecycle and #1572 native Depop address Ask chat.

Additional source-only leads awaiting fixture browser reproduction: photo-delete refusal may lack seller feedback; cleared Errors empty-state claims no warnings/errors since startup even after entries were cleared; marketplace bulk serial copy disagrees with concurrent List/Update runner. No speculative product fix or passed status from source review.

Actual CLI fixture: `/tmp/anteroom-audit-restore-AiCJb8`, sandbox=true, data-dir explicit and port4599 unused; no server launched, no production data. Created tiny SQLite audit_marker table. Actual restore-db.sh Cancel exited0 and preserved current-fixture; --yes exited0 and recovered snapshot-fixture. Decompressed pre-restore backup independently and verified current-fixture. Invalid database exited1 without changing restored marker; missing file exited66. Invalid file produced a raw sqlite3.DatabaseError traceback (recovery-copy finding, not a claim that corrupt data was restored). Summary artifact local /tmp/anteroom-audit-restore-result.json; no private/raw diagnostic export committed. Old schema migration and app startup remain separate untested scenario.

Coordinator's explicit reservation chain from current messages: #1554 full check → #1559 bounded WebKit diagnosis/full validation → hosted-pool replay → audit interactive4349/5194,30–45min first phase. Actual eBay process seen and preserved; no audit browser/PG/aggregate launched. Mac mini reachable/fullaccess with zero active Mac workers, but coordinator says native assignment belongs to parent; no device/build/isolation verified or app launched. Native remains blocked.

Totals now **49 PASS / 0 FAIL / 8 BLOCKED / 49 NOT RUN**,106 scenarios. Counts include precisely scoped API/CLI cases, not an all-controls claim. Do not end the comprehensive audit at this checkpoint: next executable UI phase waits for explicit queue handover; then use the precise source/control plans in scenarios.tsv.


## Legacy recovery and process persistence — resumed 15:11 UTC

Continued on audit/depop-durable-identity, preserving all drafts and previous evidence. Read-only process scan confirmed eBay resumed desktop/browser stage owns port5194. Reconfirmed explicit eBay→1559→hosted-pool→audit handover chain with both eBay and coordinator; asked that pool explicitly notify/resume audit rather than silently drop its reservation. No audit browser, app server, Postgres or aggregate suite started. Only bounded single-process fixtures below.

Legacy regression RED: disposable saved Depop published record with listing_url removed still returned published. Read projection now interprets this Depop shape as unknown/uncertain in record/status and inventory summaries, excludes it from live revision reads and refuses blind retry. Query/record reads leave the stored row and updated_at untouched. Explicit Check result recovers a valid product URL through the existing operation without another publish. No cleanup, migration, RLS/grant or real-record mutation. Projection deliberately limited to Depop: eBay may retain durable identity in another field and its established accounting behavior remains unchanged. Automatic reconcile still reads raw unsettled states, so recovery of this legacy case requires explicit Check; missing historical operation/job requires separate manual-verification recovery and is not silently repaired.

Independent static review found no blocker in the read-only projection/explicit recovery, with automatic-reconcile and process-test limitations stated. Final scope was narrowed to Depop after inspecting eBay identity consumers; counter-regression proves eBay ID records are unchanged.

Process persistence upgraded from same-process/new-client checks: SQLite backup of the fixture DB opened in separate Python processes using the real app and FastAPI startup/shutdown, sandbox=true and socket connects forbidden. Before simulated delist the restarted API retained the exact product URL/ID and live state; afterwards another restarted app returned deleted. Independent fake-marketplace HTTP endpoint separately returned listed before and absent after. No actual listener/browser, hosted deployment, packaged Mac or live marketplace. Initial subprocess failure was fixture Host configuration, corrected with the same trusted loopback address and an isolated4599 configuration; no Host/security guard disabled.

Validation: legacy RED1→GREEN1; intermediate affected-file run108 PASS/2 FAIL (new subprocess Host setup and an old positive fixture missing identity), then corrected affected files **111 PASS/18 subtests** with six existing SQLite datetime deprecations. Actual app lifespan version of the process case then passed independently. Final narrowed-scope run **34 PASS** (legacy/process/facets/eBay-accounting selection); Ruff/diff checks passed. Postgres/full suites remain remote CI or separately queued, not local pass claims.

Current canonical totals **51 PASS / 0 FAIL / 8 BLOCKED / 48 NOT RUN**,107 named cases. Full UI matrix, stale/interrupted browser saves, templates/bulk/photos/assistant/settings work remains pending explicit interactive handover; native Mac/iOS still blocked on parent routing. No production action or merge.
