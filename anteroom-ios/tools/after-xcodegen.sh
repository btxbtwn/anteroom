#!/bin/bash
# Runs after `xcodegen` (project.yml's postGenCommand), in this folder.
# 1. Pins every Swift package to the versions in Config/Package.resolved.
# 2. Sets the app version from vendoo-studio/VERSION, the version the whole
#    product ships as, through Config/Version.xcconfig (not tracked).
set -euo pipefail
cd "$(dirname "$0")/.."
resolved_dir=Anteroom.xcodeproj/project.xcworkspace/xcshareddata/swiftpm
mkdir -p "$resolved_dir"
cp Config/Package.resolved "$resolved_dir/Package.resolved"
version="$(tr -d '[:space:]' < ../vendoo-studio/VERSION)"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "vendoo-studio/VERSION is not a version: $version" >&2
  exit 1
fi
printf '// Written by tools/after-xcodegen.sh from vendoo-studio/VERSION.\nMARKETING_VERSION = %s\n' "$version" > Config/Version.xcconfig
