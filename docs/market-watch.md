# Marketplace and competitor watch

Where eBay, Etsy, Depop, Poshmark and Mercari announce changes to their APIs,
sell forms, policies and fees, and where competing cross-listing tools record
what they ship. Check these before changing a marketplace integration, when a
fill or API call starts failing, and on a regular review. Listed and checked
2026-11-23; links and bot-block notes rechecked the same day.

This file lists sources. It does not copy their content: policies, fees and
forms change, so read the live page and note the date you read it. Downloaded
API contracts live in [marketplace-apis](marketplace-apis/README.md).

"Blocks bots" means the site answers automated requests with 403 or a
"verify you are human" page. Some sites do this only some of the time; keep
the note on a page that blocked once. Read it in a browser or through a search
engine's copy; do not work around the block.

## When something changed

| What changed | What to do |
| --- | --- |
| A sell form (fields, labels, options, order) | Run that marketplace's form sweep (`anteroom-extension/tools/form-sweep/<marketplace>/`) and its `compare.js`; update the native form builder and the value matrix (see `AGENTS.md`, **Verification**). |
| An API (new version, field, deprecation, decommission date) | Refresh the contract snapshot as [marketplace-apis/README.md](marketplace-apis/README.md) describes, and file an issue with the decommission date in the title. |
| A policy, the API terms or the fees | File an issue that quotes the change, links the page and gives the date it takes effect. Check that Anteroom's behavior, fee figures and privacy policy still hold, and that the publishing invariant in `AGENTS.md` is not affected. |
| A competitor feature | File a Feature issue only if a seller would want it; link the competitor's dated changelog entry or store listing as evidence. Study the flow, do not copy its look or text. |

## Marketplaces

### eBay

| Kind | Source |
| --- | --- |
| API docs | [APIs](https://developer.ebay.com/develop) (blocks bots); [Sell APIs release notes](https://developer.ebay.com/develop/api/sell/release_notes), one combined page for Inventory, Account and the other Sell APIs (its content is JSON inside the HTML, about 5 MB); [Trading API release notes](https://developer.ebay.com/devzone/xml/docs/releasenotes.html); [Modern APIs migration guide](https://developer.ebay.com/develop/guides/sell/modern-apis-migration-guide), which maps each Trading call to its replacement |
| API changes | [API updates](https://developer.ebay.com/updates/api-updates) (blocks bots), [deprecation status](https://developer.ebay.com/develop/get-started/api-deprecation-status) (blocks bots) and its [RSS feed](https://developer.ebay.com/rss/api-deprecation) (machine-readable), [developer newsletter](https://developer.ebay.com/updates/newsletter) (the index blocks bots; each quarter's page, such as [q3_2026](https://developer.ebay.com/updates/newsletter/q3_2026), does not), [API status](https://developer.ebay.com/develop/api-status) |
| API terms | [API License Agreement](https://developer.ebay.com/join/api-license-agreement) (blocks bots) |
| Seller news | [Seller Center news](https://www.ebay.com/sellercenter/news) and its monthly pages, such as [2026-august](https://www.ebay.com/sellercenter/news/2026-august); [community announcements](https://community.ebay.com/forum/announcements-57928/). The [seasonal Seller Updates](https://www.ebay.com/sellercenter/resources/seller-updates) page lists nothing after January 2025. |
| Policies | [Rules and policies](https://www.ebay.com/help/policies/default/ebays-rules-policies?id=4205), [User Agreement](https://www.ebay.com/help/policies/member-behaviour-policies/user-agreement?id=4259) (both block bots) |
| Fees | [Selling fees](https://www.ebay.com/help/selling/fees-credits-invoices/selling-fees?id=4822) (blocks bots) |
| App | [App Store 282614216](https://apps.apple.com/us/app/id282614216) |

### Etsy

| Kind | Source |
| --- | --- |
| API docs | [Open API v3 documentation](https://developers.etsy.com/documentation/) and [reference](https://developers.etsy.com/documentation/reference/) |
| API changes | [etsy/open-api](https://github.com/etsy/open-api): [discussions](https://github.com/etsy/open-api/discussions) carry Etsy's API announcements, [releases](https://github.com/etsy/open-api/releases) its spec versions. Read the Announcements category of the discussions: the releases stopped at 2026-03-24 while the spec kept changing, so also check the `Last-Modified` header of the spec file. |
| API terms | [API Terms of Use](https://www.etsy.com/legal/api) (blocks bots) |
| Seller news | [Community announcements](https://community.etsy.com/forum/announcements-290/), [Seller Handbook](https://www.etsy.com/seller-handbook) (blocks bots) |
| Policies and fees | [Legal and policies](https://www.etsy.com/legal/), [Fees and payments policy](https://www.etsy.com/legal/fees/) (both block bots) |
| App | [Etsy Seller, App Store 1534619962](https://apps.apple.com/us/app/id1534619962) |

### Depop

| Kind | Source |
| --- | --- |
| API docs | [Partner API](https://partnerapi.depop.com/api-docs/), [fees concept](https://partnerapi.depop.com/api-docs/concepts/fees/) |
| API changes | [Partner API changelog](https://partnerapi.depop.com/api-docs/changelog/) and its [RSS feed](https://partnerapi.depop.com/api-docs/rss.xml) |
| News | [Depop newsroom](https://news.depop.com) |
| Policies | [Terms of service](https://www.depop.com/legal/terms-of-service/) (blocks bots) |
| Fees | [Seller fees and charges](https://depophelp.zendesk.com/hc/en-gb/articles/360001791127) (blocks bots) |
| App | [App Store 518684914](https://apps.apple.com/us/app/id518684914) |

### Poshmark

No public seller API; Anteroom uses its signed-in web pages, so the form sweep is the main check.

| Kind | Source |
| --- | --- |
| News | [Poshmark blog](https://blog.poshmark.com) and its [feed](https://blog.poshmark.com/feed/) (rate-limited: it answers 429 to repeated reads) |
| Categories | The public [catalog](https://poshmark.com/vm-rest/meta/catalog_v2); its `updated_at` changes when Poshmark changes its categories |
| Policies | [Terms of service](https://poshmark.com/terms) (served compressed: read it with `curl --compressed`), [Posh Protect](https://poshmark.com/posh_protect) |
| Fees | The fee table is in the help center, which refused automated connections on 2026-11-23. The [Poshmark blog](https://blog.poshmark.com/) states the current fee in its seller FAQ posts; confirm it in the help center in a browser. |
| App | [App Store 470412147](https://apps.apple.com/us/app/id470412147) |

### Mercari (US)

No public US seller API; Anteroom uses its signed-in web pages, so the form sweep is the main check.

| Kind | Source |
| --- | --- |
| News | [Mercari group news](https://about.mercari.com/en/press/news/) (mostly Japan) |
| Policies | [Terms of service](https://www.mercari.com/us/help_center/topics/account/policies/terms-of-service/) (blocks bots) |
| Fees | [Fees on Mercari](https://www.mercari.com/us/help_center/article/169) and [fee structure changes](https://mercari.com/us/help_center/article/2517) (block bots) |
| Cross-listing | [Importing listings from other sites](https://www.mercari.com/us/help_center/article/609/) (blocks bots) |
| App | [App Store 896130944](https://apps.apple.com/us/app/id896130944) |

## Competitors

Most do not keep a dated changelog. Their Chrome Web Store listings show the
extension's version and last update date, and App Store listings show the
version history with release notes, so those are the dependable record of
when something shipped. Release notes are often generic ("bug fixes"); a
version bump says that something shipped, and the product's help center or
changelog says what. Apple's lookup API returns the same as JSON:
`https://itunes.apple.com/lookup?id=<id>&country=us` (`version`,
`currentVersionReleaseDate`, `releaseNotes`).

| Product | Changelog or news | Help | Chrome extension | iPhone app |
| --- | --- | --- | --- | --- |
| [Vendoo](https://vendoo.co) | [Blog](https://blog.vendoo.co/) | [Help center](https://help.vendoo.co) | [mnampbajndaipakjhcbbaihllmghlcdf](https://chromewebstore.google.com/detail/mnampbajndaipakjhcbbaihllmghlcdf) | [1612168777](https://apps.apple.com/us/app/id1612168777) |
| [List Perfectly](https://listperfectly.com) | [Blog](https://listperfectly.com/blog/) (monthly reseller news) and its [feed](https://listperfectly.com/feed/) | — | [flpmljgbaphneikdjhmekdpiamkejfon](https://chromewebstore.google.com/detail/flpmljgbaphneikdjhmekdpiamkejfon) | — |
| [Crosslist](https://crosslist.com) | [Dated changelog](https://feedback.crosslist.com/en/changelogs) | — | [knfhdmkccnbhbgpahakkcmoddgikegjl](https://chromewebstore.google.com/detail/knfhdmkccnbhbgpahakkcmoddgikegjl) | [6756124351](https://apps.apple.com/us/app/id6756124351) |
| [Nifty](https://nifty.ai) | [What's New](https://docs.nifty.ai/whats-new) (dated) | [docs.nifty.ai](https://docs.nifty.ai) | [ggcfdkgmekpddencdddinlmpekbcohoa](https://chromewebstore.google.com/detail/ggcfdkgmekpddencdddinlmpekbcohoa) (listed as "Nifty – Passwordless Authenticator") | — |
| [Flyp](https://tools.joinflyp.com) (serves only an app shell to automated reads) | — | — | [kbflhgfmfbghhjafnjbgpiopcdjeajio](https://chromewebstore.google.com/detail/kbflhgfmfbghhjafnjbgpiopcdjeajio) | — |
| PrimeLister | — | [docs.primelister.com](https://docs.primelister.com) | [eepbhjeldlodgnndnjofcnnoampciipg](https://chromewebstore.google.com/detail/eepbhjeldlodgnndnjofcnnoampciipg) | [6478108527](https://apps.apple.com/us/app/id6478108527) |
| OneShop | — | — | [pcapaniacmdmabfopeeimmpjkkjpeiok](https://chromewebstore.google.com/detail/pcapaniacmdmabfopeeimmpjkkjpeiok) (not updated since 2021) | [1577487407](https://apps.apple.com/us/app/id1577487407) |
| Sell The Flip | — | — | [cnldmcpgoipcimgjjklpbnhikcnglmhm](https://chromewebstore.google.com/detail/cnldmcpgoipcimgjjklpbnhikcnglmhm) | [6832095041](https://apps.apple.com/us/app/id6832095041) |

What their customers complain about and ask for, and where to read it, is in
[competitor feedback](competitor-feedback.md).

Comparison posts on competitors' blogs are marketing; use them to learn what
a competitor claims, not as evidence of what it does. Check a claim in the
product's own help center, store listing or changelog.

## Keep this list current

Fix a moved link when you find it, add a source when a marketplace or
competitor starts publishing a new one, and remove one that stopped. Note
when a page blocks automated reads. Never copy seller data, cookies or
signed-in pages into this repository.
