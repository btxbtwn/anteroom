# Competitor customer feedback

What sellers say about competing cross-listing tools: what breaks for them and
what they ask for. Use it to choose and shape work, and cite it in an issue as
evidence that sellers care. The competitors and their release history are in
[market watch](market-watch.md).

Reviews are self-selected: unhappy and delighted customers write them more
often than everyone else. Treat a theme as a signal when it repeats across
products and sources, not because of one angry review. Do not copy a
competitor's text, design or feature list. Solve the seller's problem in
Anteroom's own way and within the publishing and delisting rules in `AGENTS.md`.

## Where the feedback is

| Source | How to read it | Notes |
| --- | --- | --- |
| App Store reviews | `https://itunes.apple.com/us/rss/customerreviews/page=<1-10>/id=<app id>/sortby=mostrecent/json`: rating, date, app version, title and text. App IDs are in [market watch](market-watch.md#competitors). | Public JSON. Returns at most the 500 newest reviews. |
| Chrome Web Store reviews | `https://chromewebstore.google.com/detail/<extension id>/reviews`: the average rating, the rating count and the newest reviews. | Only the newest reviews are on the page. Publisher replies are mixed in. |
| [Crosslist feature board](https://feedback.crosslist.com/en/feature-requests) and [roadmap](https://feedback.crosslist.com/en/roadmap) | Public requests sorted by votes, with a status (Under consideration, Planned, In progress). | The only competitor with a public, voted board. |
| Trustpilot, for example [Vendoo](https://www.trustpilot.com/review/vendoo.co) and [Crosslist](https://www.trustpilot.com/review/crosslist.com) | Read in a browser. | Blocks bots. |
| Reddit: r/Flipping, r/poshmark, r/Mercari, r/eBaySellers, r/Depop | Read in a browser, or search the web for `site:reddit.com <tool> crosslist`. | Blocks bots. |
| Google Play reviews | Read in a browser. | No public feed. |

Do not sign up for, log in to or scrape competitors' products. Read only what
is public, and keep reviewers' names out of this repository.

## First pass, 2026-11-23

**Sample:**
- App Store reviews, read in full: Vendoo (227 reviews, 2.4 stars average), SellRaze (400, 3.1), PrimeLister (378, 4.6), OneShop (137, 3.1, none since 2024) and Crosslist (13, 4.8).
- Chrome Web Store pages: Vendoo (3.5 from 73 ratings), List Perfectly (4.1 from 22), Flyp (4.2 from 416) and Crosslist (4.1 from 31).
- The Crosslist feature board.

Counts below are 1–3 star App Store reviews since 2025-05-01 that mention the
theme, found by keyword and then read. A review can count under more than one
theme.

### What breaks for sellers

| Theme | Vendoo (86 low) | SellRaze (178 low) | Also seen |
| --- | --- | --- | --- |
| Sale detection or delisting misses, so an item sells twice and an order is cancelled | 17 | 9 | List Perfectly and Vendoo Chrome reviews; SellRaze: "over 50" double sales after 250 items |
| Billing: charged after cancelling a trial, no refunds, hard to cancel, no App Store billing | 33 | 68 | PrimeLister (6), Crosslist Chrome review |
| Support is slow, scripted or a bot only | 21 | 30 | PrimeLister, List Perfectly, Crosslist |
| Crashes, freezes, stuck logging in, slow | 18 | 58 | PrimeLister, Crosslist |
| Needs a desktop; the phone app does only part of the job | 11 | 4 | Flyp ("I only wish I can carry it my phone"); PrimeLister praised for working on the phone |
| Marketplace connections fail or drop | 6 | 5 | Crosslist ("install the extension" when it is installed) |
| A marketplace was dropped after the seller paid (Vinted, Mercari) | 1 | 5 | |
| Listing caps and upsells after purchase | 3 | 17 | |
| AI gets details wrong (size, color, brand "unbranded", price comps) | 4 | 7 | PrimeLister relist changed "lime green" to "yellow" |
| Edits and quantity changes do not reach the marketplaces | 5 | 4 | SellRaze: price and shipping differ per marketplace after posting |

Representative reviews (App Store, dated):

- Vendoo, 2026-10-06: "If it sells on another platform you need to rush as fast as you can to manually delist … I've had to cancel two orders this month."
- Vendoo, 2026-07-21: the app keeps failing to delist items and to update quantities, "leading to selling out of stock items."
- SellRaze, 2026-11-15: "After listing only 250 items we noticed double sales on multiple platforms."
- Vendoo, 2026-07-29: "It only lets you post on eBay and another Site from your phone, everything else has to be done from a desktop."
- Vendoo, 2026-10-17: "Vinted was dropped and now Mercari is only available on desktop."

### What sellers ask for

The Crosslist board, by votes on 2026-11-23:

| Votes | Request | Crosslist status |
| --- | --- | --- |
| 137 | Listing variations (size, scale) with quantity and stock synced across marketplaces | In progress |
| 98 | Detect duplicate listings and SKUs before they double-sell | Planned |
| 90 | Add Vestiaire Collective | Under consideration |
| 83 | Add Instagram Shopping | Under consideration |
| 71 | See each marketplace's price, quantity, shipping and link in the listings table, and which marketplaces an item is missing from | Under consideration |
| 70 | Set the eBay Promoted Listings rate while cross-posting | Under consideration |
| 67 | End or archive listings (sold or inactive) instead of deleting them, chosen per marketplace | Under consideration |

Requests repeated in reviews:
- bulk editing, such as marking many items sold or pending (SellRaze);
- scheduled listing (Flyp);
- relisting that removes the old listing instead of leaving a duplicate (PrimeLister);
- categories that match each marketplace exactly (Crosslist);
- a human to talk to (List Perfectly, Vendoo);
- pricing tied to what the seller actually sells (PrimeLister).

### What it means for Anteroom

The common thread is trust. Sellers forgive a missing feature. They leave over:
- a missed delist that forces a cancelled order;
- a charge they did not expect;
- a support channel that does not answer.

Open questions, for an audit to check against the code and the sandbox:

1. Does every sale Anteroom detects lead to a visible delist result per marketplace, and does a missed one stay in front of the seller until it is resolved? See `services/sale_delist_followup.py` and the "still listed" alert.
2. Can a seller do the whole job from the iPhone app, or does any step still need the Mac?
3. Variations and per-marketplace quantity: what Anteroom supports today for eBay variation groups and multi-quantity items, and where it stops.
4. Duplicate detection when creating or importing a listing.
5. Ending or archiving instead of deleting, per marketplace.
6. A listings view showing each marketplace's price, quantity and link, and where an item is not listed.
7. Whether trial, cancellation and refund terms are plain before payment, and whether cancelling is one step.
8. Whether every connection failure tells the seller which marketplace and what to do.

Each confirmed gap becomes its own issue, citing this page and the dated reviews or votes.

## Keep it current

The weekly market watch (issue #1896) adds new App Store and Chrome Web Store
reviews and the Crosslist board's top requests to its snapshot. Rewrite the
first pass here when the themes shift, and keep the date.
