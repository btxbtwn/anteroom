#!/bin/bash
# Standalone Swift model checks shared by local and Xcode Cloud builds.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${RUNNER_TEMP:-}" ]]; then
  RUNNER_TEMP="$(mktemp -d)"
  trap 'rm -rf "$RUNNER_TEMP"' EXIT
fi

echo "Test stream framing"
xcrun swiftc Sources/API/StreamFrame.swift Tests/StreamFramesTests.swift -o "$RUNNER_TEMP/stream-tests"
"$RUNNER_TEMP/stream-tests"

echo "Test history contracts"
xcrun swiftc Sources/API/HistoryModels.swift Tests/HistoryModelsTests.swift -o "$RUNNER_TEMP/history-tests"
"$RUNNER_TEMP/history-tests"

echo "Test seller input preservation"
xcrun swiftc Sources/Inputs/SellerInputs.swift Tests/SellerInputsTests.swift -o "$RUNNER_TEMP/seller-input-tests"
"$RUNNER_TEMP/seller-input-tests"

echo "Test inventory rules and lossless listing edits"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/API/MarketplaceReadiness.swift Sources/Inventory/InventoryFilters.swift Sources/Inventory/Suggestions.swift Tests/InventoryTests.swift -o "$RUNNER_TEMP/inventory-tests"
"$RUNNER_TEMP/inventory-tests"

echo "Test thumbnail sizes and cache keys"
xcrun swiftc Sources/Photos/ThumbnailSize.swift Tests/ThumbnailSizeTests.swift -o "$RUNNER_TEMP/thumbnail-size-tests"
"$RUNNER_TEMP/thumbnail-size-tests"

echo "Test native marketplace forms"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/API/NativeFormModels.swift Sources/API/EbayFormModels.swift Tests/NativeFormTests.swift -o "$RUNNER_TEMP/native-form-tests"
"$RUNNER_TEMP/native-form-tests"

echo "Test eBay form rules, variations and categories"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/API/NativeFormModels.swift Sources/API/EbayFormModels.swift Tests/EbayFormTests.swift -o "$RUNNER_TEMP/ebay-form-tests"
"$RUNNER_TEMP/ebay-form-tests"

echo "Test marketplace records and eBay totals"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/Marketplaces/MarketplaceModels.swift Tests/MarketplaceTests.swift -o "$RUNNER_TEMP/marketplace-tests"
"$RUNNER_TEMP/marketplace-tests"

echo "Test eBay action payloads"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/Marketplaces/MarketplaceModels.swift Sources/Marketplaces/EbayActionModels.swift Tests/EbayActionTests.swift -o "$RUNNER_TEMP/ebay-action-tests"
"$RUNNER_TEMP/ebay-action-tests"

echo "Test analytics contract and wording"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/Analytics/AnalyticsModels.swift Tests/AnalyticsTests.swift -o "$RUNNER_TEMP/analytics-tests"
"$RUNNER_TEMP/analytics-tests"

echo "Test photo crop arithmetic"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/Photos/CropMath.swift Tests/CropMathTests.swift -o "$RUNNER_TEMP/crop-tests"
"$RUNNER_TEMP/crop-tests"

echo "Test automation requests"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/Automations/AutomationModels.swift Tests/AutomationTests.swift -o "$RUNNER_TEMP/automation-tests"
"$RUNNER_TEMP/automation-tests"

echo "Test eBay offers, sharing and offers-to-likers setup"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/Automations/AutomationModels.swift Tests/MarketplaceAutomationTests.swift -o "$RUNNER_TEMP/marketplace-automation-tests"
"$RUNNER_TEMP/marketplace-automation-tests"

echo "Test business assistant contract and wording"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/API/AssistantModels.swift Sources/Automations/AutomationModels.swift Sources/Assistant/BusinessAssistantModels.swift Tests/BusinessAssistantTests.swift -o "$RUNNER_TEMP/business-assistant-tests"
"$RUNNER_TEMP/business-assistant-tests"

echo "Test listing assistant JSON display"
xcrun swiftc Sources/Assistant/AssistantDisplay.swift Tests/AssistantDisplayTests.swift -o "$RUNNER_TEMP/assistant-display-tests"
"$RUNNER_TEMP/assistant-display-tests"

echo "Test appearance preference contract"
xcrun swiftc Sources/API/AppearancePreferences.swift Tests/AppearancePreferencesTests.swift -o "$RUNNER_TEMP/appearance-tests"
"$RUNNER_TEMP/appearance-tests"

echo "Test List, Update, Save draft and Delist rows and confirmations"
xcrun swiftc Sources/API/Generated/APIContract.swift Sources/API/Models.swift Sources/Inventory/InventoryFilters.swift Sources/API/MarketplaceReadiness.swift Sources/API/MarketplaceActionModels.swift Tests/MarketplaceActionsTests.swift -o "$RUNNER_TEMP/marketplace-action-tests"
"$RUNNER_TEMP/marketplace-action-tests"

echo "Test assistant Markdown blocks"
xcrun swiftc Sources/Assistant/MarkdownBlocks.swift Tests/MarkdownBlocksTests.swift -o "$RUNNER_TEMP/markdown-tests"
"$RUNNER_TEMP/markdown-tests"

echo "Test sign-in messages"
xcrun swiftc Sources/Session/SignInMessages.swift Tests/SignInMessagesTests.swift -o "$RUNNER_TEMP/sign-in-tests"
"$RUNNER_TEMP/sign-in-tests"

echo "Test which failed sign-in renewals sign out"
xcrun swiftc Sources/Session/SignInRenewal.swift Tests/SignInRenewalTests.swift -o "$RUNNER_TEMP/sign-in-renewal-tests"
"$RUNNER_TEMP/sign-in-renewal-tests"

echo "Test eBay and Etsy sign-in return links"
xcrun swiftc Sources/Session/MarketplaceSignInReturn.swift Tests/MarketplaceSignInReturnTests.swift -o "$RUNNER_TEMP/marketplace-sign-in-tests"
"$RUNNER_TEMP/marketplace-sign-in-tests"

echo "Test marketplace readiness and List everywhere choices"
xcrun swiftc Sources/API/MarketplaceReadiness.swift Tests/MarketplaceReadinessTests.swift -o "$RUNNER_TEMP/readiness-tests"
"$RUNNER_TEMP/readiness-tests"
