#!/bin/bash
# Build a separate, offline simulator app from production layout components.
# Install with the device tooling; never use this app to verify backend flows.
set -euo pipefail
cd "$(dirname "$0")/.."
review_output="${1:-$(mktemp -d /tmp/anteroom-layout.XXXXXX)}"
mkdir -p "$review_output/LayoutReview.app"
layout_sources=()
while IFS= read -r -d '' source_file; do
  layout_sources+=("$source_file")
done < <(find Sources -name '*.swift' ! -path '*/Session/*' ! -name AnteroomApp.swift ! -name WorkspaceView.swift -print0)
xcrun --sdk iphonesimulator swiftc -swift-version 6 -D LAYOUT_REVIEW \
  -target "$(uname -m)-apple-ios17.0-simulator" \
  -sdk "$(xcrun --sdk iphonesimulator --show-sdk-path)" \
  "${layout_sources[@]}" Tests/LayoutReviewApp.swift -o "$review_output/LayoutReview.app/LayoutReview"
python3 - "$review_output/LayoutReview.app/Info.plist" <<'PY'
import plistlib
import sys
with open(sys.argv[1], 'wb') as output:
    plistlib.dump({
        'CFBundleIdentifier': 'com.btxbtwn.anteroom.layoutreview',
        'CFBundleExecutable': 'LayoutReview',
        'CFBundleName': 'LayoutReview',
        'CFBundlePackageType': 'APPL',
        'MinimumOSVersion': '17.0',
        'UIDeviceFamily': [1],
        'UILaunchScreen': {},
    }, output)
PY
printf '%s\n' "$review_output/LayoutReview.app"
