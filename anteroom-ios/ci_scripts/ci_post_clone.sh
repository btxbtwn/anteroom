#!/bin/bash
# Xcode Cloud invokes this after cloning, before resolving Swift packages.
set -euo pipefail
: "${CI_PRIMARY_REPOSITORY_PATH:?Xcode Cloud repository path is required}"
: "${CI_BUILD_NUMBER:?Xcode Cloud build number is required}"
if [[ ! "$CI_BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]]; then
  echo 'CI_BUILD_NUMBER must be a positive integer.' >&2
  exit 1
fi
cd "$CI_PRIMARY_REPOSITORY_PATH/anteroom-ios"
key="${SUPABASE_PUBLISHABLE_KEY:-}"
if [[ -n "$key" && ! "$key" =~ ^sb_publishable_[A-Za-z0-9_-]+$ ]]; then
  echo 'SUPABASE_PUBLISHABLE_KEY must be a Supabase publishable key.' >&2
  exit 1
fi
if [[ "${CI_XCODEBUILD_ACTION:-}" == archive && -z "$key" ]]; then
  echo 'Release archives require SUPABASE_PUBLISHABLE_KEY in the cloud workflow.' >&2
  exit 1
fi
tools="$(mktemp -d)"
trap 'rm -rf "$tools"' EXIT
./tools/install-xcodegen.sh "$tools"
export PATH="$tools/xcodegen/bin:$PATH"
if grep -rn 'unitTestOnly' Sources --include='*.swift' | grep -v '^Sources/Editor/SellerConfirmation.swift:'; then
  echo 'App code must not call SellerConfirmation.unitTestOnly.' >&2
  exit 1
fi
plutil -lint Sources/PrivacyInfo.xcprivacy
./tools/test-models.sh
xcodegen
printf '// Written by Xcode Cloud.\nCURRENT_PROJECT_VERSION = %s\nSUPABASE_PUBLISHABLE_KEY = %s\n' \
  "$CI_BUILD_NUMBER" "$key" > Config/Cloud.xcconfig
