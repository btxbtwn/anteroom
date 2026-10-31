# Anteroom for iPhone

A native SwiftUI app on the hosted Anteroom API (`vendoo_studio.hosted:app`)
and Supabase, step 5 of [`docs/cloud-architecture.md`](../docs/cloud-architecture.md).

What works now: email-link sign-in, draft inventory with search, editing a
draft's title, notes and General fields, and adding photos from the camera or
library. It does what the hosted API allows and nothing more: no generation,
marketplace tabs or publishing.

## Interface

The native screens follow the workspace's neutral light/dark surfaces, blue
accent, compact inventory rows, and bordered General editor fields. Sign-in
uses the hosted workspace's wording. General fields follow the canonical
`skills/list-this/references/listing-editor-fields.json` order and labels.
Photos, labels and private notes remain available alongside them.

This is the first visual alignment pass, not desktop feature parity. The
hosted API still does not expose generation or the full marketplace workflow.

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
