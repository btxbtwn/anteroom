# Marketplace and competitor watch

Where eBay, Etsy, Depop, Poshmark and Mercari announce changes to their APIs,
sell forms, policies and fees, and where competing cross-listing tools record
what they ship. Check these before changing a marketplace integration, when a
fill or API call starts failing, and on a regular review. Listed and checked
2026-11-23.

This file lists sources. It does not copy their content: policies, fees and
forms change, so read the live page and note the date you read it. Downloaded
API contracts live in [marketplace-apis](marketplace-apis/README.md).

"Blocks bots" means the site answers automated requests with 403. Read it in
a browser or through a search engine's copy; do not work around the block.

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
| API docs | [APIs](https://developer.ebay.com/develop/apis); per-API release notes, such as [Inventory](https://developer.ebay.com/api-docs/sell/inventory/release-notes.html) and [Account](https://developer.ebay.com/api-docs/sell/account/release-notes.html); [Trading API release notes](https://developer.ebay.com/devzone/xml/docs/releasenotes.html) |
| API changes | [API updates](https://developer.ebay.com/updates/api-updates), [deprecation status](https://developer.ebay.com/develop/get-started/api-deprecation-status) (blocks bots) and its [RSS feed](https://developer.ebay.com/rss/api-deprecation) (machine-readable), [developer newsletter](https://developer.ebay.com/updates/newsletter), [API status](https://developer.ebay.com/develop/api-status) |
| API terms | [API License Agreement](https://developer.ebay.com/join/api-license-agreement) |
| Seller news | [Seller Center news](https://www.ebay.com/sellercenter/news), [seasonal Seller Updates](https://www.ebay.com/sellercenter/resources/seller-updates), [community announcements](https://community.ebay.com/t5/Announcements/bd-p/Announcements) |
| Policies | [Rules and policies](https://www.ebay.com/help/policies/default/ebays-rules-policies?id=4205), [User Agreement](https://www.ebay.com/help/policies/member-behaviour-policies/user-agreement?id=4259) |
| Fees | [Selling fees](https://www.ebay.com/help/selling/fees-credits-invoices/selling-fees?id=4822) |
| App | [App Store 282614216](https://apps.apple.com/us/app/id282614216) |

### Etsy

| Kind | Source |
| --- | --- |
| API docs | [Open API v3 documentation](https://developers.etsy.com/documentation/) and [reference](https://developers.etsy.com/documentation/reference/) |
| API changes | [etsy/open-api](https://github.com/etsy/open-api): [discussions](https://github.com/etsy/open-api/discussions) carry Etsy's API announcements, [releases](https://github.com/etsy/open-api/releases) its spec versions |
| API terms | [API Terms of Use](https://www.etsy.com/legal/api) (blocks bots) |
| Seller news | [Community announcements](https://community.etsy.com/t5/Announcements/bd-p/announcements), [Seller Handbook](https://www.etsy.com/seller-handbook) (blocks bots) |
| Policies and fees | [Legal and policies](https://www.etsy.com/legal/), [Fees and payments policy](https://www.etsy.com/legal/fees/) (both block bots) |
| App | [Etsy Seller, App Store 1534619962](https://apps.apple.com/us/app/id1534619962) |

### Depop

| Kind | Source |
| --- | --- |
| API docs | [Partner API](https://partnerapi.depop.com/api-docs/), [fees concept](https://partnerapi.depop.com/api-docs/concepts/fees/) |
| API changes | [Partner API changelog](https://partnerapi.depop.com/api-docs/changelog/) |
| News | [Depop newsroom](https://news.depop.com) |
| Policies | [Terms of service](https://www.depop.com/legal/terms-of-service/) (blocks bots) |
| Fees | [Seller fees and charges](https://depophelp.zendesk.com/hc/en-gb/articles/360001791127) (blocks bots) |
| App | [App Store 518684914](https://apps.apple.com/us/app/id518684914) |

### Poshmark

No public seller API; Anteroom uses its signed-in web pages, so the form sweep is the main check.

| Kind | Source |
| --- | --- |
| News | [Poshmark blog](https://blog.poshmark.com) |
| Policies | [Terms of service](https://poshmark.com/terms), [Posh Protect](https://poshmark.com/posh_protect) |
| Fees | The fee table is in the help center, which refused automated connections on 2026-11-23; find it from [poshmark.com](https://poshmark.com/) in a browser. |
| App | [App Store 470412147](https://apps.apple.com/us/app/id470412147) |

### Mercari (US)

No public US seller API; Anteroom uses its signed-in web pages, so the form sweep is the main check.

| Kind | Source |
| --- | --- |
| News | [Mercari group news](https://about.mercari.com/en/press/news/) (mostly Japan) |
| Policies | [Terms of service](https://www.mercari.com/us/help_center/topics/account/policies/terms-of-service/) (blocks bots) |
| Fees | [Fees on Mercari](https://www.mercari.com/us/help_center/article/169) and [fee structure changes](https://mercari.com/us/help_center/article/2517) (block bots) |
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
| [Vendoo](https://vendoo.co) | [Blog](https://vendoo.co/blog) | [Help center](https://help.vendoo.co) | [mnampbajndaipakjhcbbaihllmghlcdf](https://chromewebstore.google.com/detail/mnampbajndaipakjhcbbaihllmghlcdf) | [1612168777](https://apps.apple.com/us/app/id1612168777) |
| [List Perfectly](https://listperfectly.com) | [Blog](https://listperfectly.com/blog/) (monthly reseller news) | — | [flpmljgbaphneikdjhmekdpiamkejfon](https://chromewebstore.google.com/detail/flpmljgbaphneikdjhmekdpiamkejfon) | — |
| [Crosslist](https://crosslist.com) | [Dated changelog](https://feedback.crosslist.com/en/changelogs) | — | [knfhdmkccnbhbgpahakkcmoddgikegjl](https://chromewebstore.google.com/detail/knfhdmkccnbhbgpahakkcmoddgikegjl) | [6756124351](https://apps.apple.com/us/app/id6756124351) |
| [Nifty](https://nifty.ai) | [Blog](https://nifty.ai/blog) | — | Not located | — |
| [Flyp](https://tools.joinflyp.com) | — | — | [kbflhgfmfbghhjafnjbgpiopcdjeajio](https://chromewebstore.google.com/detail/kbflhgfmfbghhjafnjbgpiopcdjeajio) | — |
| PrimeLister | — | — | [eepbhjeldlodgnndnjofcnnoampciipg](https://chromewebstore.google.com/detail/eepbhjeldlodgnndnjofcnnoampciipg) | [6478108527](https://apps.apple.com/us/app/id6478108527) |
| OneShop | — | — | [pcapaniacmdmabfopeeimmpjkkjpeiok](https://chromewebstore.google.com/detail/pcapaniacmdmabfopeeimmpjkkjpeiok) (not updated since 2021) | [1577487407](https://apps.apple.com/us/app/id1577487407) |
| Sell The Flip | — | — | [cnldmcpgoipcimgjjklpbnhikcnglmhm](https://chromewebstore.google.com/detail/cnldmcpgoipcimgjjklpbnhikcnglmhm) | — |

Comparison posts on competitors' blogs are marketing; use them to learn what
a competitor claims, not as evidence of what it does. Check a claim in the
product's own help center, store listing or changelog.

## Keep this list current

Fix a moved link when you find it, add a source when a marketplace or
competitor starts publishing a new one, and remove one that stopped. Note
when a page blocks automated reads. Never copy seller data, cookies or
signed-in pages into this repository.
