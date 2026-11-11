#!/bin/bash
# Run before manually starting the Xcode Cloud TestFlight workflow.
set -euo pipefail
: "${ANTEROOM_IPAD_TEST_DESTINATION:?Set an iPad simulator destination for the release check}"
cd "$(dirname "$0")/.."
./tools/test-models.sh
xcodegen
xcodebuild test -project Anteroom.xcodeproj -scheme AnteroomCloud \
  -destination "${ANTEROOM_TEST_DESTINATION:-platform=iOS Simulator,name=iPhone 17}" \
  -onlyUsePackageVersionsFromResolvedFile CODE_SIGNING_ALLOWED=NO
./test-sandbox.sh
ANTEROOM_TEST_DESTINATION="$ANTEROOM_IPAD_TEST_DESTINATION" \
  ANTEROOM_TEST_ONLY=AnteroomUITests/IPadSandboxTests ./test-sandbox.sh
