# Anteroom in the cloud: Mac, web and iPhone in sync

Status: approved 2026-10-26, with Supabase and Fly.io. Building step 2: hosted sign-in and the shared desktop React workspace. Inventory, General editing, seller notes, private photos and revision history use seller-scoped hosted endpoints. The separate cloud draft editor has been removed.
Postgres support and the photo-store interface have merged (#790, #787).
The hosted session-verification foundation is described in
[hosted-auth.md](hosted-auth.md). The
[seller ownership schema](seller-ownership.md) assigns ownership to existing
and new records. [Hosted database isolation](seller-isolation.md) adds restricted
seller sessions and row policies; seller-specific constraints protect draft routes. Photos now have private Storage
scoping; account linking/import and other file-backed services remain separate work.

## Shared workspace port

The hosted entry point mounts `src/app/App.tsx` behind Supabase sign-in. It
installs the authenticated transport used by the existing API client; private
images use that transport too. Each account gets a new query client, and an
account change reloads the document to clear module-level stores. The desktop
entry keeps its local transport and full capabilities.

`GET /api/runtime` declares the capabilities that have hosted implementations.
The current cloud workspace supports inventory, General fields, notes, photo
upload/view/delete/order, listing history/restore, and per-seller preferences.
The existing Poshmark browser reader remains a read-only preview in the shared
marketplace layout. A draft edit never queues generation or browser actions.

The remaining port is backend work, rather than more frontend screens:

- Move provider settings and generation to seller-scoped Vault and Storage.
- Add reviewed, seller-approved marketplace write contracts to the Chrome Bridge.
- Port settings, account linking/import, analytics and other file-backed services
  before enabling their existing pages.
- Connect the Mac worker for the Mac-only capabilities (see **Who can do what**).

The hosted app does not expose the desktop HTTP server, Keychain, filesystem,
loopback extension socket, or desktop startup/background services. Local
inventory remains local until the account linking/import step is implemented.

## Goal

One seller account, three apps (Mac, web, iPhone), the same listings on all of
them, live. Nothing is lost if a Mac is lost.

The account is an email and password in Supabase Auth. Signing in decides whose
data every app shows: the web app and the Mac app show the same hosted web app,
and the iPhone app talks to the same API. New accounts confirm their email
before the first sign-in; a forgotten password is reset by email link.

## Why the backend moves, instead of syncing databases

Today Anteroom is one FastAPI process per Mac: SQLite on disk, photos in a
folder, secrets in Keychain, about twenty background timers, all for one seller.
Keeping several SQLite copies in step with a cloud copy would mean resolving
conflicts in every one of its 30-odd tables, forever.

Established crosslisting products keep one copy on their servers, and every
device is a client. Their phone apps queue form work that a Chrome extension
runs on the seller's computer the next time it checks in. Anteroom adopts the
same shape.

## The shape

```
            ┌──────────── Supabase ────────────┐
 iPhone ───►│ Auth (email, Apple, Google)      │
 Web    ───►│ Postgres (+ row-level security)  │◄──► Anteroom API (FastAPI, hosted)
 Mac UI ───►│ Storage (photos)                 │        listing generation, comps,
            │ Realtime (live updates)          │        marketplace APIs, job queue
            └──────────────────────────────────┘                 ▲
                                                                  │ outbound only
                                                      Chrome extension on any computer
                                                      (Poshmark, Mercari, Depop, Sellwild
                                                      form fills); Mac worker for
                                                      Mac-only work
```

- **Supabase** holds accounts, the database, photos and live updates. It has
  official SDKs for Swift, JavaScript and Python, and Sign in with Apple, which
  the App Store requires when other social sign-ins are offered.
- **Anteroom API** is today's FastAPI app, made multi-tenant and hosted. It keeps
  doing generation, category resolution, comps, eBay and Etsy, and the job queue.
  It verifies the Supabase session on every request.
- **Web app** is today's React frontend, served from the host, signed in with
  Supabase. Polling gives way to Realtime subscriptions.
- **Desktop Chrome** will connect the hosted web app directly to the installed
  extension for seller-approved marketplace form work in that browser. This
  [browser connection and consent](cloud-extension.md) are available; marketplace
  command contracts and approval/concurrency checks remain before actions ship.
  A Mac worker is needed only for desktop capabilities kept in the Mac app or
  for requesting that Mac’s browser work from another device.
- **Mac app** shows the same web app in its window once the seller signs in
  (Account › My Account; Account › This Mac's Listings goes back), and remembers
  which one it showed last. It also runs the **Mac worker** for Mac-only work. It
  connects outbound to the API, so the loopback-only rule still holds on the Mac.
- **iPhone app** is a native SwiftUI app on the same API and Supabase: photos,
  generation, editing, and queuing jobs for the Mac.

## Invariants that change (approved, now in AGENTS.md)

- "No cloud hosting" and "Studio binds only to 127.0.0.1": the API is hosted;
  the Mac worker still binds nothing and connects outbound.
- "API keys remain in macOS Keychain": the web and iPhone apps generate
  listings too, so the seller's provider keys and sign-in tokens move to the
  server, encrypted at rest (Supabase Vault), never returned to any client.
- SQLite becomes Postgres. Alembic stays the owner of the schema.

Unchanged: automation never publishes; publishing and delisting need the
seller's explicit request with `confirm`; one automation job at a time per
seller; human approval before a Send.

## Who can do what

The design does not assume a Mac.

| Seller has | What works |
|---|---|
| iPhone or web only | Everything that runs on the server: generation, editing, photos, history, analytics. eBay and Etsy list through their official APIs. |
| Any computer with desktop Chrome | The above, plus Poshmark, Mercari, Depop and Sellwild form fills through the Bridge extension, which has the only path to those forms (they offer no public listing API). |
| The Mac app | The above, plus Mac-only work: Apple Vision background removal and local SDK sign-ins. |

A phone-only seller cannot fill browser-only marketplace forms. Running a
browser on the server with the seller's marketplace sign-ins would store those
sign-ins on a server and trip marketplace bot checks, so it is not planned.

## Next steps (2026-10-31)

1. **Accounts and the Mac.** Email-and-password accounts on web, Mac and
   iPhone. The Mac app signs in and shows the account in its window.
2. **Bring a Mac's listings along.** The first time a Mac with local listings
   signs in, it offers once to add them to the account (a `VACUUM INTO`
   snapshot plus photos, mapping the local owner to the account). It never
   claims records without the seller confirming. The local copy stays as a
   backup.
3. **Fills from the web app.** Reviewed Bridge command contracts, so the web
   app fills marketplace forms in the Chrome it runs in, on any computer.
4. **Queue from the phone.** The extension pulls approved fills whenever Chrome
   is open. Every fill still needs the seller's approval and stops at a draft.
5. **Live updates.** Supabase Realtime replaces polling.

The Mac worker then covers only the Mac-only work above.

## Original order of work

Each step ships on its own and leaves a working product.

1. **Postgres and accounts, still on the Mac.** Run the API against Postgres,
   add `user_id` to seller tables and a session check, move photos behind a
   storage interface (folder today, Supabase Storage next). The Mac app keeps
   working for one seller throughout.
2. **Hosted API and web app.** Deploy the API, serve the frontend, sign in with
   Supabase. Each Mac uploads its SQLite database and photos once into the
   seller's account (`VACUUM INTO` snapshot, then import).
3. **Mac worker.** Split the extension bridge and Chrome automation out of the
   API into the worker, fed by the job queue. Replace polling with Realtime.
4. **Secrets server-side.** Provider keys, ChatGPT and marketplace tokens move
   from Keychain to Vault, so web-started generation works.
5. **iPhone app.** SwiftUI on the same API, then App Store submission.

## Decisions

- **Hosting for the API:** Fly.io (decided). The API needs long requests and
  background jobs, which rules out serverless functions, so it ships as a
  Docker image.
- **Accounts to create:** Supabase (free tier for development), Fly.io, and the
  Apple Developer Program. Not needed until step 2.
- **Price:** hosted generation, storage and bandwidth cost money per seller. A
  subscription on iPhone has to use Apple's in-app purchase.
