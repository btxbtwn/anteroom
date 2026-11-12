# iPhone CI with Xcode Cloud

Xcode Cloud owns iPhone builds, standalone Swift checks, XCTest unit tests and
TestFlight distribution. Full iPhone/iPad sandbox UI tests run locally before
releases. Mac packaging and the web/backend CI keep their existing workflows.
The self-hosted runner proposal is not part of this setup.

## One-time activation

Apple requires the first setup in Xcode. Sign into the Apple Developer team
in Xcode Settings → Accounts, then:

Keep this migration on its feature branch until the first cloud verification
passes. The GitHub workflow on main remains available until then; do not merge
its removal before proving the cloud replacement.

1. From a pushed feature-branch checkout containing these changes, run `xcodegen` in `anteroom-ios`
   and open `Anteroom.xcodeproj`. Select the **AnteroomCloud** scheme and use
   Integrate → Create Workflow. Grant Apple's GitHub integration
   access to this repository. Keep the repository's current visibility.
2. Connect the project path `anteroom-ios/Anteroom.xcodeproj` and the
   **AnteroomCloud** scheme. The project remains generated and untracked;
   `anteroom-ios/ci_scripts/ci_post_clone.sh` recreates it after each clone,
   before Swift package resolution. The scripts must be pushed before a cloud
   build can use them. Use Xcode 26.6 and the corresponding available macOS.
3. Create **iOS Verify**: trigger relevant pull requests and main changes;
   include `anteroom-ios/`, `contracts/`, backend API code and
   `vendoo-studio/VERSION`. Add a required Test action on one iPhone simulator.
   Select **AnteroomCloud**, with scheme settings. A Test action already
   builds the app; do not add a duplicate Build action. No distribution
   post-action or production credentials are needed.
4. Create **iOS Release**: manual starts on **main** only. Add the required
   Test action and an Archive action, both using **AnteroomCloud**. Add the
   `SUPABASE_PUBLISHABLE_KEY` environment variable containing the app's
   `sb_publishable_...` key. Never use a secret/service-role key. Set the
   archive's deployment preparation to TestFlight and configure the intended
   internal tester group in its distribution post-action. Apple manages signing.
5. Start **iOS Verify** and inspect its full results. Check that the hook runs
   every standalone model test and that XCTest executes the unit tests.
   Confirm the first release's marketing version matches Studio and its build
   number is newer than any existing upload for that version. Adjust the
   product's cloud build numbering before uploading if necessary. Only then
   rely on Xcode Cloud for release builds. Remove obsolete GitHub `ASC_*`
   secrets after confirming no other integration uses them.

The hook validates the cloud build number, installs checksum-verified XcodeGen,
runs all standalone model checks, checks the privacy manifest and seller
confirmation calls, regenerates the project and writes ignored cloud build
settings. Archives fail if their public Supabase key is absent or malformed.
Unit tests use URLProtocol stubs, without a live seller account.

## Activation status — 2026-11-10

Apple's GitHub connection is active. [Build 4](https://appstoreconnect.apple.com/teams/cacb4d81-675e-4fbe-99e2-fed2f3e1edb9/xcode-cloud/products/505F107A-8E64-4DF5-A50D-AA8539FB0D49/builds/03b38858-31ed-4897-ad30-87d03d31f1f9/summary)
passed on migration commit `e18b746`: 63/63 unit tests, five minutes of compute,
using Xcode 26.6 and macOS Tahoe 26.5.1. Ten compiler warnings remain in the test
code; there were no test failures. The generated, untracked project works in
the live cloud build.

**iOS Verify** currently watches only `xcode-cloud-ios`, with one iPhone 17
destination and no distribution. Switch it to the relevant main/PR changes
described above after the migration is merged. The enrollment-created
**Default** workflow is deactivated to prevent duplicate main builds.

**iOS Release** is saved but deactivated until the migration reaches main.
It allows manual starts on exactly `main`, requires an `AnteroomCloud` Test
action on one iPhone 17, archives for internal TestFlight testing, and targets
the existing **Me** group. Its publishable Supabase key is configured and
redacted. No archive or upload has been run: signing, upload processing and
the uploaded version/build number still require verification on the first
release after the local release gate passes.

Create new actions in Xcode if the web editor has not yet discovered the
generated schemes. After the first successful build, the web editor also
offers `AnteroomCloud`.

## Before each TestFlight release

Install the normal backend developer dependencies in `vendoo-studio/.venv`.
Choose an available iPad destination in Xcode and run, from the repository root:

```sh
ANTEROOM_IPAD_TEST_DESTINATION='platform=iOS Simulator,name=<your iPad simulator>' \
  ./anteroom-ios/tools/verify-release.sh
```

This runs the standalone model checks, unit tests, iPhone sandbox UI tests and
iPad layout tests. It uses disposable sandbox data, never the seller's app.
Start **iOS Release** on the same main commit only after every check passes.
Cloud cannot enforce the local test result; the manual release operator owns
that gate. A cloud green check alone does not establish full UI coverage.

## Why UI tests stay local

Apple separates build-for-testing from test execution; the source checkout is
not present in the execution phase. Our UI tests use a Python/Postgres sandbox
on loopback port 4320. A server started by a build hook would not be available
on the test machine. `AnteroomCloud` therefore includes only unit tests;
`Anteroom` retains the full suite. No paid staging service is introduced.

## Cost control

Use the 25 compute hours/month included with existing Developer Program
membership. Keep the included plan, monitor usage in App Store Connect, use
one simulator for routine verification and start release workflows manually.
Actual usage must be measured before claiming this fits the allowance.
GitHub's $0 spending cap still applies to the remaining GitHub workflows.

Apple references: [custom hooks](https://developer.apple.com/documentation/xcode/writing-custom-build-scripts),
[test execution phases](https://developer.apple.com/documentation/xcode/configuring-your-xcode-cloud-workflow-s-actions),
and [included compute](https://developer.apple.com/xcode-cloud/).
