# Account deletion

A signed-in seller can delete their Anteroom account from the web app (account
menu → **Delete account…**) and from the iPhone app (Settings → Account →
**Delete account…**). App Store guideline 5.1.1(v) requires this for apps that
let people create an account. The Mac app has no account of its own, so its
local API has no deletion route.

## What is deleted

`DELETE /api/account` on the hosted API, with the body `{"confirm": true}`,
deletes for the verified seller only:

- every row they own in every seller table (listings and their history, photos'
  records, jobs, sales and expenses, automations, sourcing, assistant chats,
  settings and caches). The list is every model table with a `user_id`, the
  same tables `seller_owner` row security protects; a test fails if the
  database has a seller table the deletion does not cover;
- every photo and chat image in the private `anteroom-photos` bucket under
  their user ID, removed through the Storage API with their own session;
- their Vault secrets (AI keys, eBay and Etsy sign-ins, relay registration)
  and the references to them;
- their relay registration: the relay forgets the device, the eBay and Etsy
  accounts linked to it and any events waiting for it;
- their Mac and Chrome device routes;
- their Supabase Auth user, which also ends every session and refresh token.

## What is not deleted

- **Live marketplace listings.** Deleting the account never lists, updates or
  delists anything. Listings stay on eBay, Poshmark, Mercari, Depop and Etsy;
  the seller ends them there if they want them down. Both confirmations say so.
- **eBay and Etsy app authorizations on the marketplace side.** Neither offers
  an API to revoke a seller's token. Anteroom deletes its copies and unlinks the
  accounts from its relay; the seller can also remove Anteroom from the
  marketplace's connected-apps settings.
- **Data on the seller's Mac.** The Mac app's local database and photos stay on
  that Mac.

## Safety rules

- The request is refused (409) while a marketplace job is queued or running,
  or a marketplace action is waiting for its result. Nothing is deleted then.
  Running generation and assistant answers are not a reason to refuse: their
  task rows are deleted, which ends their write lease.
- Every step can run again. A failure part-way (Storage or the relay offline,
  a database error) answers 503, and repeating the request finishes the rest.
  The seller's access token stays valid until it expires, so a retry still
  authenticates after the Auth user is gone.
- Seller A can never delete seller B: the route takes only the verified JWT
  subject, the request body accepts nothing but `confirm`, table deletes run
  in the row-secured seller session, and each private function reads the owner
  from that session's verified claim.
- Other signed-in devices cannot refresh their session once the Auth user is
  deleted. An access token already issued stays valid until it expires (see
  [hosted sessions](hosted-auth.md)); anything a device writes in that window
  is not removed automatically.

## How the Auth user is deleted without a service-role key

The hosted API holds no service-role key. Revision `7c3e9a1d5b20` adds three
`SECURITY DEFINER` functions in `anteroom_private`, owned by the migration
account and executable only by the restricted `anteroom_seller` role:

- `account_deletion_ready()` checks that the owner may delete from
  `auth.users` and `vault.secrets` and read `storage.objects`. When it may not,
  the API answers 503 before deleting anything.
- `account_photo_keys()` lists the seller's object names in the photo bucket.
  Supabase refuses direct deletes from `storage.objects`, so the API removes
  them through the Storage API.
- `delete_account()` deletes the seller's Vault secrets (by their
  `anteroom:<user id>:` name), secret references and device routes, then the
  `auth.users` row.

Each function takes the owner from `anteroom_private.seller_id()`, the verified
claim the API sets on the transaction, and refuses without one. On Supabase
the migration runs as `postgres`, which may delete from `auth.users`. Where a
table does not exist (CI's Postgres, the local sandboxes), that step is skipped.

## When eBay deletes a seller's eBay account

eBay's [Marketplace Account Deletion](https://developer.ebay.com/marketplace-account-deletion)
policy asks every app to delete the data it holds about an eBay user once eBay
reports the account closed. The relay (`webhook-relay/`) verifies the
notification, unlinks the account and passes only the eBay user ID to the
seller whose connected account it names; buyers' accounts never reach
Anteroom, and Anteroom keeps no buyer names, addresses or messages from eBay.

`services/ebay_account_deletion.py` runs on the Mac (SQLite, Keychain) and on
the web (the seller's own database session under row security, Vault), in a
worker thread. When the notice names the connected eBay account it deletes:

- the eBay sign-in: tokens, eBay user ID and username;
- the saved eBay business policies and item locations;
- each item's eBay listing record (`marketplace_listings` rows for eBay:
  listing, offer and SKU IDs, eBay photo URLs, eBay's messages);
- eBay action receipts and eBay offer rules, approvals and sends kept under
  that account's ID.

It keeps the seller's own items, photos and listing text, and the sales ledger
(order and line numbers, dates and amounts, no buyer details), which is the
seller's record of their own income and which tax rules require them to keep.
The sign-in is deleted last, so a failure part way is retried in full, and a
replayed notice finds no matching account and does nothing. The log line counts
what was deleted and names no account.
