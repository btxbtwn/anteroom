# Webhook relay

Anteroom's relay, deployed at `https://relay.anteroom.workers.dev`. A Cloudflare
Worker that gives eBay and Etsy one public HTTPS address, so every seller's
Anteroom hears about their sales without ever accepting a connection from
outside their Mac. It also holds the two app secrets a desktop app must not
ship: eBay's Cert ID and Etsy's shared secret.

- **One inbox per Mac.** Each Anteroom registers as a device and gets its own
  token (the relay keeps only a hash). Marketplace accounts are linked to the
  device that signed them in, and their events go only to that device's inbox.
- **Checks every webhook is genuine.** eBay: ECDSA signature in
  `X-EBAY-SIGNATURE`, verified against the key from eBay's Notification API.
  Etsy: Standard Webhooks HMAC with a 300 s replay window.
- **Keeps IDs only** (order, line item, listing, shop, receipt, seller user ID).
  Anteroom fetches the details itself, so buyer names, addresses and prices
  never sit in the cloud. Unacknowledged events wait 30 days.
- **Brokers sign-in.** eBay and Etsy only redirect to HTTPS, and eBay's code
  exchange needs the Cert ID, so the relay hosts the callback, exchanges the
  code, links the account, and hands the tokens to the device once (held at
  most 15 minutes). Refreshes go through the relay for the same reason. For
  eBay it also subscribes the seller to `ORDER_CONFIRMATION`.
- **Forwards Etsy API calls** with the app's `x-api-key`, only for Etsy
  accounts linked to the calling device. Nothing is stored.
- **Never acts.** It records that something happened. Marking an item sold or
  delisting stays in Anteroom, and delisting still needs the seller to ask.

## Endpoints

| Path | Caller | What it does |
|---|---|---|
| `GET /ebay?challenge_code=` | eBay | Endpoint challenge |
| `POST /ebay` | eBay | Notifications: sales, account deletion |
| `POST /etsy` | Etsy | Order, listing and inventory webhooks |
| `GET /oauth/{ebay,etsy}/callback` | Seller's browser | Finishes a sign-in |
| `GET /privacy` | Anyone | What the relay keeps |
| `POST /devices` | Anteroom | Registers a device: `{deviceId, token}`. 5 per minute per IP |
| `GET /studio/stream?after=<seq>` | Anteroom | WebSocket: backlog, then live events. Send `{"type":"ack","through":<seq>}` |
| `GET /studio/events?after=<seq>` | Anteroom | Same events, pulled |
| `POST /studio/ack` `{"through":<seq>}` | Anteroom | Drops events up to `seq` |
| `POST /studio/oauth/{ebay,etsy}/start` | Anteroom | `{state, authorizeUrl}` to open in the browser |
| `GET /studio/oauth/result?state=` | Anteroom | 202 while pending, then the account and tokens, once |
| `POST /studio/oauth/{ebay,etsy}/refresh` `{"refreshToken"}` | Anteroom | New tokens; 401 means sign in again |
| `GET /studio/accounts` | Anteroom | Linked accounts |
| `DELETE /studio/accounts/{ebay,etsy}/<id>` | Anteroom | Unlinks one |
| `DELETE /studio/device` | Anteroom | Removes the device, its links and its inbox |
| `* /studio/etsy/v3/application/...` | Anteroom | Etsy API, with `X-Etsy-Authorization: Bearer <Etsy token>` |

`/studio/*` needs `Authorization: Bearer <device token>`. An event can arrive
twice (live, then again after an ack); dedupe by `seq`.

## Configuration

Vars live in `wrangler.jsonc`: `EBAY_ENV` and `EBAY_RUNAME` (the eBay RuName
whose accept and decline URLs are `/oauth/ebay/callback`).
Etsy's app must list `https://relay.anteroom.workers.dev/oauth/etsy/callback`
as a callback URL.

Secrets are set with `npx wrangler secret put <NAME>`, and copies sit in the
macOS Keychain under service `anteroom-relay`.

| Name | Where it comes from |
|---|---|
| `EBAY_VERIFICATION_TOKEN` | Generated; also entered in eBay's portal with the endpoint URL |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | eBay production keyset (App ID, Cert ID) |
| `ETSY_KEYSTRING` | Etsy app keystring. Public by design, but kept out of the repo |
| `ETSY_SHARED_SECRET` | Etsy app shared secret |
| `ETSY_WEBHOOK_SECRET` | `whsec_...` from Etsy's webhook portal |

A marketplace whose secrets are missing answers 503.

## Commands

```
npm install
npm test          # signature unit tests, then the Worker tests in workerd
npx wrangler dev  # local, reads .dev.vars (gitignored)
npx wrangler deploy
```
