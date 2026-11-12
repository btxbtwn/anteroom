# Anteroom for iPhone

A native SwiftUI app on the hosted Anteroom API (`vendoo_studio.hosted:app`)
and Supabase, step 5 of [`docs/cloud-architecture.md`](../docs/cloud-architecture.md).

What works now: email-and-password accounts (sign in, create account, and
password reset by email link, which reopens the app through `anteroom://`), draft inventory with search, editing a
draft's title, notes and General fields, and adding photos from the camera or
library. The listing assistant connects native generation and text chat to the
hosted durable-task API when the server advertises generation and has a provider.
Each marketplace tab has its own List or Update button; List everywhere, and
Save draft and Delist per marketplace, work from a listing's actions menu; see
"Listing and delisting" below. A draft with photos and no description shows
Generate listing under its photos.

## Interface

The native screens keep iOS's structure (tab bar, navigation stacks, grouped
lists, swipe actions, segmented controls) and take the web app's look from
`Sources/App/WorkspaceStyle.swift`, which copies the hosted workspace's color
tokens: the neutral `#0A0A0A` page and `#111111` panels in dark mode, zinc
surfaces in light mode, the web's ink accent and status colors, bordered text
fields, the web's status tabs and its chat composer. Grouped lists use
`.workspaceGroupedList()` with `.listRowBackground(WorkspaceStyle.surface)`
on their rows; a List does not pass a row background down by itself. Sign-in
uses the hosted workspace's wording. General fields follow the canonical
`skills/list-this/references/listing-editor-fields.json` order and labels.
Photos, labels and private notes remain available alongside them.

This is the first visual alignment pass, not desktop feature parity. The
hosted API still does not expose generation or the full marketplace workflow.

## iPhone navigation

Inventory and Settings are native tabs, each with its own navigation stack.
New listing is a modal task, not a tab: Cancel writes nothing, Create runs
once while the sheet is busy, and success opens the new draft after dismissal.
Settings shows the signed-in account and installed version, with confirmation
before signing out. Inventory keeps its search and open listing across tabs.

## iPad

The same app runs on iPad (iOS 18 or later). The tabs use
`.sidebarAdaptable`: a tab bar that also opens as a sidebar. In a
regular-width window, Inventory is a `NavigationSplitView` with the list on
the left and the open listing on the right. In a narrow window (Split View,
Stage Manager) it falls back to the iPhone's push navigation, and the open
listing stays open across the switch. An editor that closes without Save or
Discard (another listing opened, a layout switch, a tab switch) keeps its
unsaved edits on the device, and they come back when that listing reopens.
The local release checks run `IPadSandboxTests` on an iPad simulator.

Fields expose their labels to VoiceOver; price/cost and photo controls stack
at accessibility text sizes. Photo deletion is a visible action with
confirmation, rather than a long-press-only gesture. Photos save immediately;
listing text still saves with Save.

Device review: check tab switching with an open edited draft, Cancel/Create
(including a failed request and retry), the largest accessibility text size,
VoiceOver field labels, photo add/delete, and light/dark appearance.

## Build on a Mac

CI builds with Xcode 26.6 and XcodeGen 2.46.0
(`tools/install-xcodegen.sh` installs that release after checking its
checksum); use the same versions locally.

```sh
anteroom-ios/tools/install-xcodegen.sh "$HOME/.local/xcodegen" && export PATH="$HOME/.local/xcodegen/xcodegen/bin:$PATH"
cd anteroom-ios
cp Config/Local.xcconfig.example Config/Local.xcconfig   # add the publishable key and your team ID
xcodegen
open Anteroom.xcodeproj
```

The Xcode project is generated from `project.yml` and not tracked. Change
`project.yml`, never the generated project, and run `xcodegen` again.

After generating, `tools/after-xcodegen.sh` (XcodeGen's `postGenCommand`)
does two things:

- copies `Config/Package.resolved` into the project, so every build uses the
  same Supabase (2.55.6, `exactVersion` in `project.yml`) and the same
  versions of its dependencies. To update a package, change `project.yml`,
  resolve in Xcode, and commit the new `Package.resolved` back to `Config/`.
- writes `Config/Version.xcconfig` (not tracked) with the app version from
  `vendoo-studio/VERSION`, the version the whole product ships as. The build
  number comes from the CI run.

## Sign-in

The app asks Supabase to email a link that opens `anteroom://login-callback`.
Add that URL to the Supabase project's **Auth → URL Configuration → Redirect
URLs**, or the link will open the web app instead. Open the link on the same
iPhone that asked for it.

## CI and TestFlight

Xcode Cloud builds and tests the `AnteroomCloud` scheme. Its post-clone hook
installs pinned XcodeGen, runs the standalone Swift checks under `Tests/`,
generates the project, and sets the cloud build number. The cloud scheme runs
the XCTest unit tests under `UnitTests/` without requiring the Python sandbox.
The `Anteroom` scheme retains all unit and UI tests for local verification.
See [Xcode Cloud setup](../docs/xcode-cloud.md) for account activation and the
required local iPhone/iPad checks before a manually started TestFlight release.

The unit tests stub the network with `URLProtocol`: request building, error
messages, the seller-confirmation requirement, the 401 handling below, photo
upload failure counting and kept unsaved edits.

Apple manages cloud signing. Set `SUPABASE_PUBLISHABLE_KEY` in the cloud release
workflow's environment; release archives fail if it is missing. PR verification
has no distribution post-action. GitHub's former `ASC_*` signing secrets are
no longer consumed by an iOS workflow; remove them after a successful cloud
release if nothing else uses them.

## Shared backend port

The iPhone is a native client of the hosted FastAPI backend. It never embeds
Python, stores provider credentials, or talks to the desktop HTTP server.
Generation and chat use the existing seller-authenticated workspace contracts:
`GET /api/runtime`, `/api/status`, conversation messages/activity, streaming
`POST /generate` and `/messages`, explicit `/stop`, and `resume=true` to observe
existing work without submitting it again. The server saves generated revisions;
the native editor reloads on return from the assistant. Unsaved local edits must
be saved first. Disconnecting never cancels or automatically retries work.

The first functional port adds native generation, a saved text transcript,
progress, stop, and explicit reconnect. Provider setup currently remains in the
web app. Chat attachments, live token rendering, and automatic foreground
reconnection remain to be ported. Generation requires server deployment with
the background worker and a configured provider; compilation does not verify
those production settings.

The complete feature-by-feature tracker is
[`docs/iphone-parity.md`](../docs/iphone-parity.md). It separates native
implementation, hosted backend availability and device verification.

Listing history uses the existing shared revisions API: browse saved versions,
preview General details and restore the full server snapshot with the current
revision ID as a conflict guard. Unsaved edits must be saved first. Restore
creates a new revision; it does not restore photos/private notes or publish.

Swift stream framing tests run in the iOS build workflow. Device verification
must cover a configured account generating from photos, assistant edits, an
interrupted connection followed by Reconnect, Stop, and refresh of the saved
listing without submitting duplicate work. No test or app flow publishes.

## Local sandbox and UI tests

See [agent sandboxes](../docs/agent-sandboxes.md). Run `./sandbox.sh --hosted`
from the repo root, then launch Debug with
`ANTEROOM_SANDBOX_URL=http://localhost:4320` in the Xcode scheme environment.
Release ignores this variable. `./anteroom-ios/test-sandbox.sh` starts the local
hosted backend and runs the draft creation/relaunch UI test without accounts.

## Desktop visual alignment

The General editor uses the desktop inspector's Listing, Pricing, Condition,
Item, Size, Color & material, Shipping, and Inventory & identifiers sections,
with flat dividers instead of one large card. Photos come before the fields;
assistant/history live in the native actions menu. Inventory rows use cover
photos and the same compact title/status hierarchy. Photos show six thumbnails
in an adaptive grid, with an explicit expand control for larger sets.
Thumbnails are authenticated requests to the existing shared backend.

For offline simulator layout review, run `./tools/build-layout-review.sh` on a
Mac and install the resulting `.app` with device tooling. It has its own bundle
ID and uses production row/field/section views with clearly labelled sample
data. It does not replace the signed-in app or test API behavior. The sample
photo illustrations are placeholders, not uploaded product photos. Review
light/dark appearance and larger text sizes; live cover-photo loading, uploads
and edits still need configured-account verification.

## Generated API models

The inventory row, listing, photo, save, runtime and error models in
`Sources/API/Generated/APIContract.swift` are generated from the backend's
shared contract (see [`contracts/README.md`](../contracts/README.md)); the
web app's TypeScript types come from the same source. Never edit that file:
change `vendoo-studio/server/vendoo_studio/routes/api_contract.py` and run
`python vendoo-studio/scripts/api-contract.py`. Behavior the phone adds to
those models (display titles, cover photo ids, photo edit checks) lives in
extensions in `Sources/API/Models.swift`. A field with a default decodes to
that default when a response leaves it out, and a literal (a runtime feature,
a photo turn) decodes as a plain string or number, so a value a newer server
adds never fails to decode on an older build.

## Listing and delisting

A marketplace tab opens with that marketplace's status and its next step:
"List on eBay", "Update on eBay", Cancel or Check result, through the same
confirmation as below. Fields the marketplace needs are named there and open
when tapped; a blocking one disables List until it is filled. Marketplaces
show in the web editor's order everywhere: eBay, Poshmark, Mercari, Depop, Etsy.

Listing actions → List everywhere… lists on several marketplaces at once.
Listing actions → Manage marketplaces shows each marketplace the server can act
on (`action_marketplaces` from `/api/runtime`), with the web's statuses: Listed
with a link, Not listed, Listing… with its current step, Unconfirmed, Sold
here, Delisting…, Ended or Deleted. Rules and wording come from the web's
Marketplaces pane (`MarketplacesSection.tsx`), ported in
`Sources/API/MarketplaceActionModels.swift`.

- List… sends the saved revision with `confirm` after a confirmation that
  names the marketplace. A sold item offers no List; a live one offers
  Update and Delist instead.
- Update… sends the saved revision to a live listing after a confirmation; it
  is highlighted when the live listing is behind the saved version.
- Save draft saves an unpublished draft on the marketplace wherever List is
  offered. Buyers cannot see it, so it asks no confirmation, as on the web.
- Delist… names each marketplace and what happens there (eBay ends the
  listing, the others delete it) and sends `confirm`.
- eBay and Etsy list through their APIs. Poshmark, Mercari and Depop go
  through the seller's signed-in Anteroom tab in Chrome on their computer
  (`services/hosted_browser_relay.py`); without it the server refuses and the
  row shows why.
- A running job is followed every 1.5 seconds until it ends; Cancel stops it.
  An outcome Anteroom could not confirm offers Check result, never a retry.

Nothing lists, updates or delists without the seller's tap and confirmation, and the
phone never retries either. The API calls for List, Update and Delist take a
`SellerConfirmation`, which only the confirmation dialog in
`Sources/Editor/SellerConfirmation.swift` can make, for the action and
marketplaces it named; List everywhere asks the same way. CI fails if app code
calls the test-only constructor. `Tests/MarketplaceActionsTests.swift` covers the
rows and confirmations; the hosted sandbox refuses List, Update and Delist, which the
sheet shows under the marketplace.

## Seller inputs and measurements

The General form follows the order the web uses: Photos, then Listing (Title,
Description, Price, Cost), Item (Category, Brand, Department), Condition &
details, Measurements (in), Shipping, Inventory, and Notes & Labels last. The
seller-input controls use
the desktop ItemDetails notes keys: SKU, seller notes, known flaws, labels,
cost of goods, package dimensions, garment and per-garment measurements.
Top, Pants and Skirt retain their separate values when switching. Measurements
are seller text in inches, so fractions are not converted or rounded.

Save sends the structured notes with the existing optimistic draft save.
Unedited notes are sent byte-for-byte unchanged; editing known fields preserves
unknown root fields and nested measurement values. Plain narrative notes remain
seller notes when other fields are added. No database or API schema changes are
needed. Seller inputs are evidence for generation, separate from generated
price/cost/SKU fields. Existing generated labels remain preserved by the General
save contract; the visible seller labels use desktop's `vendooLabels` notes key.
Package dimensions display saved seller values; shared preference defaults and
recent-label suggestions remain part of the settings port.

The iOS workflow runs executable seller-note preservation tests and the hosted
sandbox UI test that saves seller inputs, switches garments, relaunches the app
and checks the saved values. Screenshots are exported from XCTest results into
the `iphone-sandbox-results` workflow artifact, on success as well as failure.

## Shared appearance

Settings → Appearance offers the same `dark`, `light` and `system` choices as
the hosted workspace. The signed-in app loads `/api/settings/ui` and sends a
partial update containing only `theme`. It applies acknowledged responses,
keeps the current appearance after a failed save, and offers Reload appearance
rather than automatically replaying writes. Draft editing stays available if
loading preferences fails.

The signed-in workspace refreshes the account preference when the app becomes
active. System removes the forced color scheme so iOS controls the appearance.
There is no account-independent theme cache; signing out removes this state.
This shares the hosted account preference, not a standalone Mac installation's
local settings file. Mac-local settings remain separate until account migration.
It is foreground refresh, not instant cross-device Realtime synchronization.

The contract test checks response decoding and theme-only update payloads.
The hosted simulator test selects Light, Dark and System, relaunches for each,
and records screenshots in the `iphone-sandbox-results` artifact.

## Sign-in that ends

When the hosted API refuses the seller's token (401), the app renews the
sign-in once. A read is then sent again; a write is not, because it may have
already run, so the seller sees "Your sign-in was renewed. Try that again."
Only a refusal from Supabase signs out: no saved session, or a refresh answered
400/401/403 with `refresh_token_not_found`, `refresh_token_already_used`,
`session_not_found`, `session_expired`, `user_not_found`, `user_banned` or
OAuth's `invalid_grant` (`Sources/Session/SignInRenewal.swift`). The app then
returns to the sign-in screen. Any other failure to renew (offline, a 5xx, rate
limiting) keeps the saved sign-in and shows an error to try again. Opening the
app shows the saved session straight away, even expired or offline; requests
renew it when they can. A 401 that names a marketplace (eBay signed Anteroom
out) keeps its own message and the sign-in. Signing out also deletes exported
CSVs, cached photos and kept editor edits from the phone.

## Marketplace sign-in

Connecting eBay opens eBay's page in a web authentication sheet
(`ASWebAuthenticationSession` through SwiftUI's `webAuthenticationSession`)
that shares Safari's cookies. The relay finishes by redirecting to
`anteroom://marketplace-signin` with a one-time claim; the sheet catches that
address, closes, and the app trades the claim for the connection.

## When the server is briefly unavailable

The hosted API restarts on every deploy, and Fly's proxy answers 503 for a
while. `ReadRetry` (`Sources/API/ReadRetry.swift`) sends a read (GET) again
after a 502, 503 or 504 or a failed connection, with waits that double up to
8 seconds plus jitter, following `Retry-After`, for at most 90 seconds; the
screen keeps its loading state meanwhile. Leaving the screen cancels the wait.
Nothing else is ever sent twice: saves, generation, chat and every marketplace
action fail once and show their error. A screen still showing a load error
loads again when the app returns to the foreground (`reloadingOnReturn`).

## Kept edits and uploads

Unsaved editor edits are written to the phone when the app goes to the
background and come back when that listing is opened again, unless the listing
was saved somewhere else since; then they are dropped and the seller is told.
Photo uploads keep going for a while after switching apps, count every photo
that didn't upload ("2 of 10 photos didn't upload") and keep those photos for
Retry. Camera photos are shrunk to the upload size off the main thread and
encoded once.
