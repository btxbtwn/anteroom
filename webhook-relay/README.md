# Webhook relay

Anteroom's relay, deployed at `https://relay.anteroom.workers.dev`. A Cloudflare Worker that gives eBay and Etsy one public HTTPS address for
webhooks, so Studio can hear about sales without ever accepting a connection
from outside the Mac.

- **Checks every webhook is genuine** before keeping it. eBay: ECDSA signature
  in `X-EBAY-SIGNATURE`, verified against the key from eBay's Notification API.
  Etsy: Standard Webhooks HMAC with a 300 s replay window.
- **Keeps IDs only** (order, line item, listing, shop, receipt, user ID for
  account deletion). Studio fetches the details itself through the API, so
  buyer names, addresses and prices never sit in the cloud.
- **Studio connects out.** It holds a WebSocket to `/studio/stream` or pulls
  `/studio/events`, then acknowledges what it has handled. Unacknowledged
  events wait 30 days, so nothing is lost while the Mac sleeps.
- **Never acts.** It records that something happened. Marking an item sold or
  delisting stays in Studio, and delisting still needs the seller to ask.

## Endpoints

| Path | Who calls it | What it does |
|---|---|---|
| `GET /ebay?challenge_code=` | eBay | Answers the endpoint challenge |
| `POST /ebay` | eBay | Notifications, including account deletion |
| `POST /etsy` | Etsy | Order, listing and inventory webhooks |
| `GET /studio/stream?after=<seq>` | Studio | WebSocket: backlog, then live events. Send `{"type":"ack","through":<seq>}` |
| `GET /studio/events?after=<seq>` | Studio | Same events, pulled |
| `POST /studio/ack` `{"through":<seq>}` | Studio | Drops everything up to `seq` |

`/studio/*` needs `Authorization: Bearer <STUDIO_TOKEN>`. An event can arrive
twice (live, then again after an ack); dedupe by `seq`.

## Secrets

Set with `npx wrangler secret put <NAME>`. The two generated ones are also in
the macOS Keychain under service `anteroom-relay`.

| Name | Where it comes from |
|---|---|
| `STUDIO_TOKEN` | Generated; Studio sends it |
| `EBAY_VERIFICATION_TOKEN` | Generated; paste into eBay's portal with the endpoint URL |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | eBay production keyset (App ID, Cert ID) |
| `ETSY_WEBHOOK_SECRET` | `whsec_...` from Etsy's webhook portal |

Until a marketplace's secrets are set, its endpoint answers 503.

## Commands

```
npm install
npm test          # signature and ID-extraction tests
npx wrangler dev  # local, reads .dev.vars (gitignored)
npx wrangler deploy
```
