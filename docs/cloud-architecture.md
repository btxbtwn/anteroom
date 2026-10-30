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
- Connect the Mac worker for capabilities unavailable in a browser or on iPhone.

The hosted app does not expose the desktop HTTP server, Keychain, filesystem,
loopback extension socket, or desktop startup/background services. Local
inventory remains local until the account linking/import step is implemented.

## Goal

One seller account, three apps (Mac, web, iPhone), the same listings on all of
them, live. Nothing is lost if a Mac is lost.

## Why the backend moves, instead of syncing databases

Today Anteroom is one FastAPI process per Mac: SQLite on disk, photos in a
folder, secrets in Keychain, about twenty background timers, all for one seller.
Keeping several SQLite copies in step with a cloud copy would mean resolving
conflicts in every one of its 30-odd tables, forever.

Established crosslisting products (Vendoo, List Perfectly) keep one copy on their
servers, and every device is a client. Vendoo's iPhone app queues work that its
Chrome extension runs on the seller's computer the next time it checks in (the
extension polls `queue/pull` every five minutes). Anteroom adopts the same shape.

## The shape

```
            ┌──────────── Supabase ────────────┐
 iPhone ───►│ Auth (email, Apple, Google)      │
 Web    ───►│ Postgres (+ row-level security)  │◄──► Anteroom API (FastAPI, hosted)
 Mac UI ───►│ Storage (photos)                 │        listing generation, comps,
            │ Realtime (live updates)          │        marketplace APIs, job queue
            └──────────────────────────────────┘                 ▲
                                                                  │ outbound only
                                                      Mac worker (Chrome extension
                                                      bridge: Poshmark, Mercari,
                                                      Depop, Vendoo fills)
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
- **Mac app** shows the same web app in its window, and runs the **Mac worker**:
  the extension bridge and Chrome automation, which can only run where Chrome
  runs. It connects outbound to the API and takes queued fill jobs, so the
  loopback-only rule still holds on the Mac.
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

## Order of work

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
