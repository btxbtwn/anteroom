# Anteroom for iPhone

A native SwiftUI app on the hosted Anteroom API (`vendoo_studio.hosted:app`)
and Supabase, step 5 of [`docs/cloud-architecture.md`](../docs/cloud-architecture.md).

What works now: email-link sign-in, draft inventory with search, editing a
draft's title, notes and General fields, and adding photos from the camera or
library. The listing assistant connects native generation and text chat to the
hosted durable-task API when the server advertises generation and has a provider.
Marketplace tabs and publishing are not implemented on iPhone.

## Interface

The native screens follow the workspace's neutral light/dark surfaces, blue
accent, compact inventory rows, and bordered General editor fields. Sign-in
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

Fields expose their labels to VoiceOver; price/cost and photo controls stack
at accessibility text sizes. Photo deletion is a visible action with
confirmation, rather than a long-press-only gesture. Photos save immediately;
listing text still saves with Save.

Device review: check tab switching with an open edited draft, Cancel/Create
(including a failed request and retry), the largest accessibility text size,
VoiceOver field labels, photo add/delete, and light/dark appearance.

## Build on a Mac

Needs Xcode 16 or newer and [XcodeGen](https://github.com/yonaskolb/XcodeGen).

```sh
brew install xcodegen
cd anteroom-ios
cp Config/Local.xcconfig.example Config/Local.xcconfig   # add the publishable key and your team ID
xcodegen
open Anteroom.xcodeproj
```

The Xcode project is generated from `project.yml` and not tracked. Change
`project.yml`, never the generated project, and run `xcodegen` again.

## Sign-in

The app asks Supabase to email a link that opens `anteroom://login-callback`.
Add that URL to the Supabase project's **Auth → URL Configuration → Redirect
URLs**, or the link will open the web app instead. Open the link on the same
iPhone that asked for it.

## CI and TestFlight

`.github/workflows/ios.yml` builds for the Simulator on every change under
`anteroom-ios/`. On `main` (or when run by hand from the Actions tab) it also
archives, signs and uploads the build to TestFlight, numbered by the workflow
run. Install it on the iPhone from the TestFlight app.

Signing uses an App Store Connect API key (Admin role) stored as the
repository secrets `ASC_KEY_ID`, `ASC_ISSUER_ID` and `ASC_KEY_P8`; Xcode
creates and renews the certificate and profile with it. The publishable key
comes from the repository variable `SUPABASE_PUBLISHABLE_KEY`. Pull requests
never run the upload job.

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
=======
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

| Workflow | iPhone status |
| --- | --- |
| Sign-in, drafts, General editing, camera/library | Implemented |
| Native Inventory/Settings navigation and account | Implemented |
| Hosted generation and text assistant | Implemented; device/integration verification pending |
| Full inventory states, filtering, history/restore | Still to port |
| Provider setup, preferences, analytics, sourcing | Still to port |
| Marketplace forms and seller-approved actions | Still to port; browser connection required where applicable |

Swift stream framing tests run in the iOS build workflow. Device verification
must cover a configured account generating from photos, assistant edits, an
interrupted connection followed by Reconnect, Stop, and refresh of the saved
listing without submitting duplicate work. No test or app flow publishes.
>>>>>>> origin/main
