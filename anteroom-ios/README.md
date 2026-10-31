# Anteroom for iPhone

A native SwiftUI app on the hosted Anteroom API (`vendoo_studio.hosted:app`)
and Supabase, step 5 of [`docs/cloud-architecture.md`](../docs/cloud-architecture.md).

What works now: email-link sign-in, draft inventory with search, editing a
draft's title, notes and General fields, and adding photos from the camera or
library. It does what the hosted API allows and nothing more: no generation,
marketplace tabs or publishing.

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

## CI

`.github/workflows/ios.yml` generates the project and builds it for the
Simulator on every change under `anteroom-ios/`.
